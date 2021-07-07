# ====================== BEGIN GPL LICENSE BLOCK ======================
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ======================= END GPL LICENSE BLOCK ========================

# <pep8 compliant>

import bpy
import enum

from mathutils import Vector, Quaternion

from rigify.utils.layers import set_bone_layers
from rigify.utils.naming import NameSides, make_derived_name, get_name_base_and_sides, change_name_side, Side, SideZ
from rigify.utils.bones import BoneUtilityMixin, set_bone_widget_transform
from rigify.utils.widgets_basic import create_cube_widget, create_sphere_widget
from rigify.utils.mechanism import MechanismUtilityMixin

from rigify.utils.node_merger import MainMergeNode, QueryMergeNode

from .skin_parents import ControlBoneParentLayer, ControlBoneWeakParentLayer
from .skin_rigs import BaseSkinRig, BaseSkinChainRig


class ControlNodeLayer(enum.IntEnum):
    FREE = 0
    MIDDLE_PIVOT = 10
    TWEAK = 20


class ControlNodeIcon(enum.IntEnum):
    TWEAK = 0
    MIDDLE_PIVOT = 1
    FREE = 2
    CUSTOM = 3


class ControlNodeEnd(enum.IntEnum):
    START = -1
    MIDDLE = 0
    END = 1


def _get_parent_rigs(rig):
    result = []
    while rig:
        result.append(rig)
        rig = rig.rigify_parent
    return result


class ControlBoneNode(MainMergeNode, MechanismUtilityMixin, BoneUtilityMixin):
    """Node representing controls of skin chain rigs."""

    merge_domain = 'ControlNetNode'

    def __init__(
        self, rig, org, name, *, point=None, size=None,
        needs_parent=False, needs_reparent=False, allow_scale=False,
        chain_end=ControlNodeEnd.MIDDLE,
        layer=ControlNodeLayer.FREE, index=None, icon=ControlNodeIcon.TWEAK,
    ):
        assert isinstance(rig, BaseSkinChainRig)

        super().__init__(rig, name, point or rig.get_bone(org).head)

        self.org = org

        self.name_split = get_name_base_and_sides(name)

        self.name_merged = None
        self.name_merged_split = None

        self.size = size or rig.get_bone(org).length
        self.layer = layer
        self.icon = icon
        self.rotation = None
        self.chain_end = chain_end

        # Parent mechanism generator for this node
        self.node_parent = None
        # Create the parent mechanism even if not master
        self.node_needs_parent = needs_parent
        # If this node's own parent mechanism differs from master, generate a conversion bone
        self.node_needs_reparent = needs_reparent

        # Generate the control as a MCH bone to hide it from the user
        self.hide_control = False
        # Unlock scale channels
        self.allow_scale = allow_scale

        # For use by the owner rig: index in chain
        self.index = index
        # If this node is the end of a chain, points to the next one
        self.chain_end_neighbor = None

    def can_merge_into(self, other):
        # Only merge up the layers (towards more mechanism)
        dprio = self.rig.chain_priority - other.rig.chain_priority
        return (
            dprio <= 0 and
            (self.layer <= other.layer or dprio < 0) and
            super().can_merge_into(other)
        )

    def get_merge_priority(self, other):
        # Prefer higher and closest layer
        if self.layer <= other.layer:
            return -abs(self.layer - other.layer)
        else:
            return -abs(self.layer - other.layer) - 100

    def is_better_cluster(self, other):
        # Prefer bones that have strictly more parents
        my_parents = list(reversed(_get_parent_rigs(self.rig.rigify_parent)))
        other_parents = list(reversed(_get_parent_rigs(other.rig.rigify_parent)))

        if len(my_parents) > len(other_parents) and my_parents[0:len(other_parents)] == other_parents:
            return True
        if len(other_parents) > len(my_parents) and other_parents[0:len(other_parents)] == my_parents:
            return False

        # Prefer side chains
        side_x_my, side_z_my = map(abs, self.name_split[1:])
        side_x_other, side_z_other = map(abs, other.name_split[1:])

        if ((side_x_my < side_x_other and side_z_my <= side_z_other) or
                (side_x_my <= side_x_other and side_z_my < side_z_other)):
            return False
        if ((side_x_my > side_x_other and side_z_my >= side_z_other) or
                (side_x_my >= side_x_other and side_z_my > side_z_other)):
            return True

        return False

    def merge_done(self):
        if self.is_master_node:
            self.parent_subrig_cache = []
            self.parent_subrig_names = {}
            self.reparent_requests = []
            self.used_parents = {}

        super().merge_done()

        self.find_mirror_siblings()

    def find_mirror_siblings(self):
        self.mirror_siblings = {}
        self.mirror_sides_x = set()
        self.mirror_sides_z = set()

        for node in self.get_merged_siblings():
            if node.name_split.base == self.name_split.base:
                self.mirror_siblings[node.name_split] = node
                self.mirror_sides_x.add(node.name_split.side)
                self.mirror_sides_z.add(node.name_split.side_z)

        assert self.mirror_siblings[self.name_split] is self

        # Remove sides that merged with a mirror from the name
        side_x = Side.MIDDLE if len(self.mirror_sides_x) > 1 else self.name_split.side
        side_z = SideZ.MIDDLE if len(self.mirror_sides_z) > 1 else self.name_split.side_z

        self.name_merged = change_name_side(self.name, side=side_x, side_z=side_z)
        self.name_merged_split = NameSides(self.name_split.base, side_x, side_z)

    def get_best_mirror(self):
        base, side, sidez = self.name_split

        for flip in [(base, -side, -sidez), (base, -side, sidez), (base, side, -sidez)]:
            mirror = self.mirror_siblings.get(flip, None)
            if mirror and mirror is not self:
                return mirror

        return None

    def build_parent_for_node(self, node, use_parent=False):
        assert self.rig.generator.stage == 'initialize'

        # Build the parent
        result = node.rig.build_own_control_node_parent(node)
        parents = node.rig.get_all_parent_skin_rigs()

        for rig in reversed(parents):
            result = rig.extend_control_node_parent(result, node)

        for rig in parents:
            result = rig.extend_control_node_parent_post(result, node)

        result = self.intern_parent(node, result)
        result.is_parent_frozen = True

        if use_parent:
            self.register_use_parent(result)

        return result

    def intern_parent(self, node, parent):
        if id(parent) in self.parent_subrig_names:
            return parent

        cache = self.parent_subrig_cache

        for previous in cache:
            if previous == parent:
                previous.is_parent_frozen = True
                return previous

        cache.append(parent)
        self.parent_subrig_names[id(parent)] = node.name

        if isinstance(parent, ControlBoneParentLayer):
            parent.parent = self.intern_parent(node, parent.parent)

        return parent

    def build_parent(self):
        if not self.node_parent:
            self.node_parent = self.merged_master.build_parent_for_node(self)

        return self.node_parent

    def register_use_parent(self, parent):
        parent.is_parent_frozen = True
        self.merged_master.used_parents[id(parent)] = parent

    def request_reparent(self, parent):
        master = self.merged_master
        requests = master.reparent_requests

        if parent not in requests:
            if parent != master.node_parent or master.use_mix_parent:
                master.register_use_parent(master.node_parent)

            master.register_use_parent(parent)
            requests.append(parent)

    def get_rotation(self):
        if self.rotation is None:
            self.rotation = self.rig.get_final_control_node_rotation(self)

        return self.rotation

    def initialize(self):
        if self.is_master_node:
            sibling_list = self.get_merged_siblings()
            mirror_sibling_list = self.mirror_siblings.values()

            # Compute size
            best = max(sibling_list, key=lambda n: n.icon)
            best_mirror = best.mirror_siblings.values()

            self.size = sum(node.size for node in best_mirror) / len(best_mirror)

            # Compute orientation
            self.rotation = sum(
                (node.get_rotation() for node in mirror_sibling_list),
                Quaternion((0, 0, 0, 0))
            ).normalized()

            self.matrix = self.rotation.to_matrix().to_4x4()
            self.matrix.translation = self.point

            # Create parents
            self.node_parent_list = [node.build_parent() for node in mirror_sibling_list]

            if all(parent == self.node_parent for parent in self.node_parent_list):
                self.use_mix_parent = False
                self.node_parent_list = [self.node_parent]
            else:
                self.use_mix_parent = True

            self.has_weak_parent = isinstance(self.node_parent, ControlBoneWeakParentLayer)
            self.node_parent_base = ControlBoneWeakParentLayer.strip(self.node_parent)

            self.node_parent_list = [
                ControlBoneWeakParentLayer.strip(p) for p in self.node_parent_list]

            for parent in self.node_parent_list:
                self.register_use_parent(parent)

        # All nodes
        if self.node_needs_parent or self.node_needs_reparent:
            parent = self.build_parent()
            if self.node_needs_reparent:
                self.request_reparent(parent)

    def prepare_bones(self):
        # Activate parent components once all reparents are registered
        if self.is_master_node:
            for parent in self.used_parents.values():
                parent.enable_component()

            self.used_parents = None

    @property
    def control_bone(self):
        return self.merged_master._control_bone

    def get_reparent_bone(self, parent):
        return self.reparent_bones[id(parent)]

    @property
    def reparent_bone(self):
        return self.merged_master.get_reparent_bone(self.node_parent)

    def make_bone(self, name, scale, *, rig=None, orientation=None):
        name = (rig or self).copy_bone(self.org, name)

        if orientation is not None:
            matrix = orientation.to_matrix().to_4x4()
            matrix.translation = self.merged_master.point
        else:
            matrix = self.merged_master.matrix

        bone = self.get_bone(name)
        bone.matrix = matrix
        bone.length = self.merged_master.size * scale

        return name

    def find_master_name_node(self):
        # Chain end nodes have sub-par names, so try to find another chain
        if self.chain_end == ControlNodeEnd.END:
            siblings = [
                node for node in self.get_merged_siblings()
                if self.mirror_sides_x.issubset(node.mirror_sides_x)
                and self.mirror_sides_z.issubset(node.mirror_sides_z)
            ]

            candidates = [node for node in siblings if node.chain_end == ControlNodeEnd.START]

            if not candidates:
                candidates = [node for node in siblings if node.chain_end == ControlNodeEnd.MIDDLE]

            if candidates:
                return min(candidates, key=lambda c: (-c.rig.chain_priority, c.name_merged))

        return self

    def generate_bones(self):
        if self.is_master_node:
            # Make control bone
            self._control_bone = self.make_master_bone()

            # Make mix parent if needed
            self.reparent_bones = {}

            if self.use_mix_parent:
                self.mix_parent_bone = self.make_bone(
                    make_derived_name(self._control_bone, 'mch', '_mix_parent'), 1/2)
            else:
                self.reparent_bones[id(self.node_parent)] = self._control_bone

            self.use_weak_parent = False

            # Make requested reparents
            for parent in self.reparent_requests:
                if id(parent) not in self.reparent_bones:
                    parent_name = self.parent_subrig_names[id(parent)]
                    self.reparent_bones[id(parent)] = self.make_bone(
                        make_derived_name(parent_name, 'mch', '_reparent'), 1/3)
                    self.use_weak_parent = self.has_weak_parent

            if self.use_weak_parent:
                self.weak_parent_bone = self.make_bone(
                    make_derived_name(self._control_bone, 'mch', '_weak_parent'), 1/2)

    def make_master_bone(self):
        choice = self.find_master_name_node()
        name = choice.name_merged

        if self.hide_control:
            name = make_derived_name(name, 'mch')

        return choice.make_bone(name, 1)

    def parent_bones(self):
        if self.is_master_node:
            if self.use_mix_parent:
                self.set_bone_parent(self._control_bone, self.mix_parent_bone,
                                     inherit_scale='AVERAGE')
                self.rig.generator.disable_auto_parent(self.mix_parent_bone)
            else:
                self.set_bone_parent(self._control_bone,
                                     self.node_parent_list[0].output_bone, inherit_scale='AVERAGE')

            if self.use_weak_parent:
                self.set_bone_parent(
                    self.weak_parent_bone, self.node_parent.output_bone,
                    inherit_scale=self.node_parent.inherit_scale_mode
                )

            for parent in self.reparent_requests:
                bone = self.reparent_bones[id(parent)]
                if bone != self._control_bone:
                    self.set_bone_parent(bone, parent.output_bone, inherit_scale='AVERAGE')

    def configure_bones(self):
        if self.is_master_node:
            if not any(node.allow_scale for node in self.get_merged_siblings()):
                self.get_bone(self.control_bone).lock_scale = (True, True, True)

        layers = self.rig.get_control_node_layers(self)
        if layers:
            bone = self.get_bone(self.control_bone).bone
            set_bone_layers(bone, layers, not self.is_master_node)

    def rig_bones(self):
        if self.is_master_node:
            if self.use_mix_parent:
                targets = [parent.output_bone for parent in self.node_parent_list]
                self.make_constraint(self.mix_parent_bone, 'ARMATURE',
                                     targets=targets, use_deform_preserve_volume=True)

            for rig in reversed(self.rig.get_all_parent_skin_rigs()):
                rig.extend_control_node_rig(self)

            reparent_source = self.control_bone

            if self.use_weak_parent:
                reparent_source = self.weak_parent_bone

                self.make_constraint(reparent_source, 'COPY_TRANSFORMS',
                                     self.control_bone, space='LOCAL')

                set_bone_widget_transform(self.obj, self.control_bone, reparent_source)

            for parent in self.reparent_requests:
                bone = self.reparent_bones[id(parent)]
                if bone != self._control_bone:
                    self.make_constraint(bone, 'COPY_TRANSFORMS', reparent_source)

    def generate_widgets(self):
        if self.is_master_node:
            best = max(self.get_merged_siblings(), key=lambda n: n.icon)

            if best.icon == ControlNodeIcon.TWEAK:
                create_sphere_widget(self.obj, self.control_bone)
            elif best.icon in (ControlNodeIcon.MIDDLE_PIVOT, ControlNodeIcon.FREE):
                create_cube_widget(self.obj, self.control_bone)
            else:
                best.rig.make_control_node_widget(best)


class ControlQueryNode(QueryMergeNode, MechanismUtilityMixin, BoneUtilityMixin):
    """Node representing controls of skin chain rigs."""

    merge_domain = 'ControlNetNode'

    def __init__(self, rig, org, *, name=None, point=None, find_highest_layer=False):
        assert isinstance(rig, BaseSkinRig)

        super().__init__(rig, name or org, point or rig.get_bone(org).head)

        self.org = org
        self.find_highest_layer = find_highest_layer

    def can_merge_into(self, other):
        return True

    def get_merge_priority(self, other):
        return other.layer if self.find_highest_layer else -other.layer

    @property
    def merged_master(self):
        return self.matched_nodes[0]

    @property
    def control_bone(self):
        return self.merged_master.control_bone
