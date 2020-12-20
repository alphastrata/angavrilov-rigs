#====================== BEGIN GPL LICENSE BLOCK ======================
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
#======================= END GPL LICENSE BLOCK ========================

# <pep8 compliant>

import bpy

from rigify.utils.naming import make_derived_name
from rigify.utils.widgets_basic import create_cube_widget

from rigify.base_rig import stage

from .skin_rigs import BaseSkinRig, ControlQueryNode

from rigify.rigs.basic.raw_copy import RelinkConstraintsMixin


class Rig(BaseSkinRig, RelinkConstraintsMixin):
    """Custom skin control query node."""

    def find_org_bones(self, bone):
        return bone.name

    def initialize(self):
        super().initialize()

        self.use_tail = self.params.relink_constraints and self.params.skin_glue_use_tail
        self.relink_unmarked_constraints = self.use_tail


    ####################################################
    # CONTROL NODES

    @stage.initialize
    def init_control_nodes(self):
        bone = self.get_bone(self.base_bone)

        head_mode = self.params.skin_glue_head_mode

        self.head_position_node = PositionQueryNode(
            self, self.base_bone, point=bone.head,
            rig_org = (head_mode != 'CHILD'),
            needs_reparent = (head_mode == 'REPARENT'),
        )

        self.head_constraint_node = ControlQueryNode(
            self, self.base_bone, point=bone.head
        )

        if self.use_tail:
            self.tail_position_node = PositionQueryNode(
                self, self.base_bone, point=bone.tail,
                needs_reparent=self.params.skin_glue_tail_reparent,
            )

    def build_own_control_node_parent(self, node):
        return self.build_control_node_parent_next(node)


    ##############################
    # ORG chain

    @stage.parent_bones
    def parent_org_bone(self):
        if self.params.skin_glue_head_mode == 'CHILD':
            self.set_bone_parent(self.bones.org, self.head_position_node.output_bone)

    @stage.rig_bones
    def rig_org_bone(self):
        org = self.bones.org
        ctrl = self.head_constraint_node.control_bone

        # This executes before head_position_node owned a by generator plugin
        self.relink_bone_constraints(org)

        # Add the built-in constraint
        if self.use_tail:
            target = self.tail_position_node.output_bone
            add_mode = self.params.skin_glue_add_constraint
            inf = self.params.skin_glue_add_constraint_influence

            if add_mode == 'COPY_LOCATION':
                self.make_constraint(
                    ctrl, 'COPY_LOCATION', target, insert_index=0,
                    owner_space='LOCAL', target_space='LOCAL',
                    use_offset=True, influence=inf
                )
            elif add_mode == 'COPY_LOCATION_OWNER':
                self.make_constraint(
                    ctrl, 'COPY_LOCATION', target, insert_index=0,
                    owner_space='LOCAL', target_space='OWNER_LOCAL',
                    use_offset=True, influence=inf
                )

        # Move constraints to the control
        org_bone = self.get_bone(org)
        ctl_bone = self.get_bone(ctrl)

        for con in list(org_bone.constraints):
            ctl_bone.constraints.copy(con)
            org_bone.constraints.remove(con)

    def find_relink_target(self, spec, old_target):
        if self.use_tail and (spec == 'TARGET' or spec == '' == old_target):
            return self.tail_position_node.output_bone

        return super().find_relink_target(spec, old_target)


    ####################################################
    # SETTINGS

    @classmethod
    def add_parameters(self, params):
        params.skin_glue_head_mode = bpy.props.EnumProperty(
            name        = 'Glue Mode',
            items       = [('CHILD', 'Child Of Control',
                            "The glue bone becomes a child of the control bone"),
                           ('MIRROR', 'Mirror Of Control',
                            "The glue bone becomes a sibling of the control bone with Copy Transforms"),
                           ('REPARENT', 'Mirror With Parents',
                            "The glue bone keeps its parent, but uses Copy Transforms to group both local and parent induced motion of the control into local space")],
            default     = 'CHILD',
            description = "Specifies how the glue bone is rigged to the control at the bone head location",
        )

        params.skin_glue_use_tail = bpy.props.BoolProperty(
            name        = 'Use Tail Target',
            default     = False,
            description = 'Find the control at the bone tail location and use it to relink TARGET or any constraints without an assigned subtarget or relink spec'
        )

        params.skin_glue_tail_reparent = bpy.props.BoolProperty(
            name        = 'Target Local With Parents',
            default     = False,
            description = 'Include transformations induced by target parents into target local space'
        )

        params.skin_glue_add_constraint = bpy.props.EnumProperty(
            name        = 'Add Constraint',
            items       = [('NONE', 'No New Constraint',
                            "Don't add new constraints"),
                           ('COPY_LOCATION', 'Copy Location (Local)',
                            "Add a constraint to copy Local Location with Offset. If the owner and target control "+
                            "rest orientations are different, the global movement direction will change accordingly"),
                           ('COPY_LOCATION_OWNER', 'Copy Location (Owner Local)',
                            "Add a constraint to copy Owner Local Location with Offset. Even if the owner and target "+
                            "controls have different rest orientations, the global movement direction would be the same")],
            default     = 'NONE',
            description = "Add one of the common constraints linking the control to the tail target",
        )

        params.skin_glue_add_constraint_influence = bpy.props.FloatProperty(
            name        = "Influence",
            default     = 1.0, min=0, max=1,
            description = "Influence of the added constraint",
        )

        self.add_relink_constraints_params(params)

        super().add_parameters(params)

    @classmethod
    def parameters_ui(self, layout, params):
        layout.prop(params, "skin_glue_head_mode")
        layout.prop(params, "relink_constraints")

        if params.relink_constraints:
            col = layout.column()
            col.prop(params, "skin_glue_use_tail")

            col2 = col.column()
            col2.active = params.skin_glue_use_tail
            col2.prop(params, "skin_glue_tail_reparent")

            col = layout.column()
            col.active = params.skin_glue_use_tail
            col.prop(params, "skin_glue_add_constraint", text="Add")

            col3 = col.column()
            col3.active = params.skin_glue_add_constraint != 'NONE'
            col3.prop(params, "skin_glue_add_constraint_influence", slider=True)

        layout.label(text="All constraints are moved to the control bone.", icon='INFO')



class PositionQueryNode(ControlQueryNode):
    """Finds the position of the highest layer control and rig reparent and/or org bone"""

    def __init__(self, rig, org, *, point=None, needs_reparent=False, rig_org=False):
        super().__init__(rig, org, point=point, find_highest_layer=True)

        self.needs_reparent = needs_reparent
        self.rig_org = rig_org

    @property
    def output_bone(self):
        if self.rig_org:
            return self.org
        elif self.needs_reparent:
            return self.merged_master.get_reparent_bone(self.node_parent)
        else:
            return self.control_bone

    def initialize(self):
        if self.needs_reparent:
            self.node_parent = self.merged_master.build_parent_for_node(self, use_parent=True)

            if not self.rig_org:
                self.merged_master.request_reparent(self.node_parent)

    def parent_bones(self):
        if self.rig_org:
            if self.needs_reparent:
                parent = self.node_parent.output_bone
            else:
                parent = self.get_bone_parent(self.control_bone)

            self.set_bone_parent(self.org, parent, inherit_scale='AVERAGE')

    def apply_bones(self):
        if self.rig_org:
            self.get_bone(self.org).matrix = self.merged_master.matrix

    def rig_bones(self):
        if self.rig_org:
            self.make_constraint(self.org, 'COPY_TRANSFORMS', self.control_bone)


def create_sample(obj):
    from rigify.rigs.basic.super_copy import create_sample as inner
    obj.pose.bones[inner(obj)["Bone"]].rigify_type = 'skin.glue'
