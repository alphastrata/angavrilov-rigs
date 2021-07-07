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

from itertools import count
from string import Template

from rigify.utils.naming import make_derived_name
from rigify.utils.misc import force_lazy, LazyRef

from rigify.base_rig import LazyRigComponent, stage


class ControlBoneParentBase(LazyRigComponent):
    rigify_sub_object_run_late = True

    # This parent cannot be merged with other wrappers?
    is_parent_frozen = False

    def __init__(self, rig, node):
        super().__init__(node)
        self.rig = rig
        self.node = node

    def __eq__(self, other):
        raise NotImplementedError()


class ControlBoneParentOrg:
    """Control node parent generator wrapping a single ORG bone."""

    is_parent_frozen = True

    def __init__(self, org):
        self._output_bone = org

    @property
    def output_bone(self):
        return force_lazy(self._output_bone)

    def enable_component(self):
        pass

    def __eq__(self, other):
        return isinstance(other, ControlBoneParentOrg) and self._output_bone == other._output_bone


class ControlBoneParentArmature(ControlBoneParentBase):
    """Control node parent generator using Armature to parent the bone."""

    def __init__(self, rig, node, *, bones, orientation=None, copy_scale=None, copy_rotation=None):
        super().__init__(rig, node)
        self.bones = bones
        self.orientation = orientation
        self.copy_scale = copy_scale
        self.copy_rotation = copy_rotation
        if copy_scale or copy_rotation:
            self.is_parent_frozen = True

    def __eq__(self, other):
        return (
            isinstance(other, ControlBoneParentArmature) and
            self.node.point == other.node.point and
            self.orientation == other.orientation and
            self.bones == other.bones and
            self.copy_scale == other.copy_scale and
            self.copy_rotation == other.copy_rotation
        )

    def generate_bones(self):
        self.output_bone = self.node.make_bone(
            make_derived_name(self.node.name, 'mch', '_arm'), 1/4, rig=self.rig)

        self.rig.generator.disable_auto_parent(self.output_bone)

        if self.orientation:
            matrix = force_lazy(self.orientation).to_matrix().to_4x4()
            matrix.translation = self.node.point
            self.get_bone(self.output_bone).matrix = matrix

    def parent_bones(self):
        self.targets = force_lazy(self.bones)

        assert len(self.targets) > 0

        if len(self.targets) == 1:
            target = force_lazy(self.targets[0])
            if isinstance(target, tuple):
                target = target[0]

            self.set_bone_parent(
                self.output_bone, target,
                inherit_scale='NONE' if self.copy_scale else 'FIX_SHEAR'
            )

    def rig_bones(self):
        if len(self.targets) > 1:
            self.make_constraint(
                self.output_bone, 'ARMATURE', targets=force_lazy(self.bones),
                use_deform_preserve_volume=True
            )

            self.make_constraint(self.output_bone, 'LIMIT_ROTATION')

        if self.copy_rotation:
            self.make_constraint(self.output_bone, 'COPY_ROTATION', self.copy_rotation)
        if self.copy_scale:
            self.make_constraint(self.output_bone, 'COPY_SCALE', self.copy_scale)


class ControlBoneParentLayer(ControlBoneParentBase):
    def __init__(self, rig, node, parent):
        super().__init__(rig, node)
        self.parent = parent

    def enable_component(self):
        self.parent.enable_component()
        super().enable_component()


class ControlBoneWeakParentLayer(ControlBoneParentLayer):
    inherit_scale_mode = 'AVERAGE'

    @staticmethod
    def strip(parent):
        while isinstance(parent, ControlBoneWeakParentLayer):
            parent = parent.parent

        return parent


class ControlBoneParentOffset(ControlBoneParentLayer):
    """
    Parent mechanism generator that offsets the control's location.

    Supports Copy Transforms (Local) constraints and location drivers.
    Multiple offsets can be accumulated in the same generator, which
    will automatically create as many bones as needed.
    """

    @classmethod
    def wrap(cls, owner, parent, node, *constructor_args):
        return cls(owner, node, parent, *constructor_args)

    def __init__(self, rig, node, parent):
        super().__init__(rig, node, parent)
        self.copy_local = {}
        self.add_local = {}
        self.add_orientations = {}
        self.limit_distance = []

    def enable_component(self):
        while isinstance(self.parent, ControlBoneParentOffset) and not self.parent.is_parent_frozen:
            self.prepend_contents(self.parent)
            self.parent = self.parent.parent

        super().enable_component()

    def prepend_contents(self, other):
        for key, val in other.copy_local.items():
            if key not in self.copy_local:
                self.copy_local[key] = val
            else:
                inf, expr, cbs = val
                inf0, expr0, cbs0 = self.copy_local[key]
                self.copy_local[key] = [inf+inf0, expr+expr0, cbs+cbs0]

        for key, val in other.add_orientations.items():
            if key not in self.add_orientations:
                self.add_orientations[key] = val

        for key, val in other.add_local.items():
            if key not in self.add_local:
                self.add_local[key] = val
            else:
                ot0, ot1, ot2 = val
                my0, my1, my2 = self.add_local[key]
                self.add_local[key] = (ot0+my0, ot1+my1, ot2+my2)

        self.limit_distance = other.limit_distance + self.limit_distance

    def add_copy_local_location(self, target, *, influence=1, influence_expr=None, influence_vars={}):
        if target not in self.copy_local:
            self.copy_local[target] = [0, [], []]

        if influence_expr:
            self.copy_local[target][1].append((influence_expr, influence_vars))
        elif callable(influence):
            self.copy_local[target][2].append(influence)
        else:
            self.copy_local[target][0] += influence

    def add_location_driver(self, orientation, index, expression, variables):
        assert isinstance(variables, dict)

        key = tuple(round(x*10000) for x in orientation)

        if key not in self.add_local:
            self.add_orientations[key] = orientation
            self.add_local[key] = ([], [], [])

        self.add_local[key][index].append((expression, variables))

    def add_limit_distance(self, target, **kwargs):
        self.limit_distance.append((target, kwargs))

    def __eq__(self, other):
        return (
            isinstance(other, ControlBoneParentOffset) and
            self.parent == other.parent and
            self.copy_local == other.copy_local and
            self.add_local == other.add_local and
            self.limit_distance == other.limit_distance
        )

    @property
    def output_bone(self):
        return self.mch_bones[-1] if self.mch_bones else self.parent.output_bone

    def generate_bones(self):
        self.mch_bones = []
        self.reuse_mch = False

        if self.copy_local or self.add_local or self.limit_distance:
            mch_name = make_derived_name(self.node.name, 'mch', '_poffset')

            if self.add_local:
                for key in self.add_local:
                    self.mch_bones.append(self.node.make_bone(
                        mch_name, 1/4, rig=self.rig, orientation=self.add_orientations[key]))
            else:
                # Try piggybacking on the parent bone if allowed
                if not self.parent.is_parent_frozen:
                    bone = self.get_bone(self.parent.output_bone)
                    if (bone.head - self.node.point).length < 1e-5:
                        self.reuse_mch = True
                        self.mch_bones = [bone.name]
                        return

                self.mch_bones.append(self.node.make_bone(mch_name, 1/4, rig=self.rig))

    def parent_bones(self):
        if self.mch_bones:
            if not self.reuse_mch:
                self.rig.set_bone_parent(self.mch_bones[0], self.parent.output_bone)

            self.rig.parent_bone_chain(self.mch_bones, use_connect=False)

    def compile_driver(self, items):
        variables = {}
        expressions = []

        for expr, varset in items:
            template = Template(expr)
            varmap = {}

            try:
                template.substitute({k: '' for k in varset})
            except Exception as e:
                self.rig.raise_error('Invalid driver expression: {}\nError: {}', expr, e)

            # Merge variables
            for name, desc in varset.items():
                # Check if the variable is used.
                try:
                    template.substitute({k: '' for k in varset if k != name})
                    continue
                except KeyError:
                    pass

                # descriptors may not be hashable, so linear search
                for vn, vdesc in variables.items():
                    if vdesc == desc:
                        varmap[name] = vn
                        break
                else:
                    new_name = name
                    if new_name in variables:
                        for i in count(1):
                            new_name = '%s_%d' % (name, i)
                            if new_name not in variables:
                                break
                    variables[new_name] = desc
                    varmap[name] = new_name

            expressions.append(template.substitute(varmap))

        if len(expressions) > 1:
            final_expr = '+'.join('('+expr+')' for expr in expressions)
        else:
            final_expr = expressions[0]

        return final_expr, variables

    def rig_bones(self):
        if self.copy_local:
            mch = self.mch_bones[0]
            for target, (influence, drivers, lazyinf) in self.copy_local.items():
                influence += sum(map(force_lazy, lazyinf))

                con = self.make_constraint(
                    mch, 'COPY_LOCATION', target, use_offset=True,
                    target_space='LOCAL_OWNER_ORIENT', owner_space='LOCAL', influence=influence,
                )

                if drivers:
                    if influence > 0:
                        drivers.append((str(influence), {}))

                    expr, variables = self.compile_driver(drivers)
                    self.make_driver(con, 'influence', expression=expr, variables=variables)

        if self.add_local:
            for mch, (key, specs) in zip(self.mch_bones, self.add_local.items()):
                for index, vals in enumerate(specs):
                    if vals:
                        expr, variables = self.compile_driver(vals)
                        self.make_driver(mch, 'location', index=index,
                                         expression=expr, variables=variables)

        for target, kwargs in self.limit_distance:
            self.make_constraint(self.mch_bones[-1], 'LIMIT_DISTANCE', target, **kwargs)
