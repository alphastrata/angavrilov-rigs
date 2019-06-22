import bpy

from rigify.utils.bones import align_chain_x_axis
from rigify.utils.widgets_basic import create_circle_widget
from rigify.utils.layers import ControlLayersOption
from rigify.utils.misc import map_list

from rigify.base_rig import stage
from rigify.rigs.chain_rigs import TweakChainRig


class Rig(TweakChainRig):
    def initialize(self):
        super(Rig,self).initialize()

        self.copy_rotation_axes = self.params.copy_rotation_axes

    # Prepare
    def prepare_bones(self):
        if self.params.roll_alignment == "automatic":
            align_chain_x_axis(self.obj, self.bones.org)

    # Parent
    @stage.parent_bones
    def parent_control_chain(self):
        # use_connect=False for backward compatibility
        self.parent_bone_chain(self.bones.ctrl.fk, use_connect=False)

    # Configure
    @stage.configure_bones
    def configure_tweak_chain(self):
        super().configure_tweak_chain()

        ControlLayersOption.TWEAK.assign(self.params, self.obj, self.bones.ctrl.tweak)

    # Rig
    @stage.rig_bones
    def rig_control_chain(self):
        ctrls = self.bones.ctrl.fk
        for args in zip(ctrls, [None] + ctrls):
            self.rig_control_bone(*args)

    def rig_control_bone(self, ctrl, prev_ctrl):
        if prev_ctrl:
            self.make_constraint(
                ctrl, 'COPY_ROTATION', prev_ctrl,
                use_xyz=self.copy_rotation_axes,
                space='LOCAL', use_offset=True
            )

    # Widgets
    def make_control_widget(self, ctrl):
        create_circle_widget(self.obj, ctrl, radius=0.3, head_tail=0.5)


    @classmethod
    def add_parameters(self, params):
        """ Add the parameters of this rig type to the
            RigifyParameters PropertyGroup
        """
        params.copy_rotation_axes = bpy.props.BoolVectorProperty(
            size=3,
            description="Automation axes",
            default=tuple([i == 0 for i in range(0, 3)])
            )

        # Setting up extra tweak layers
        ControlLayersOption.TWEAK.add_parameters(params)

        items = [('automatic', 'Automatic', ''), ('manual', 'Manual', '')]
        params.roll_alignment = bpy.props.EnumProperty(items=items, name="Bone roll alignment", default='automatic')


    @classmethod
    def parameters_ui(self, layout, params):
        """ Create the ui for the rig parameters.
        """

        r = layout.row()
        r.prop(params, "roll_alignment")

        row = layout.row(align=True)
        for i, axis in enumerate(['x', 'y', 'z']):
            row.prop(params, "copy_rotation_axes", index=i, toggle=True, text=axis)

        ControlLayersOption.TWEAK.parameters_ui(layout, params)


def create_sample(obj):
    # generated by rigify.utils.write_metarig
    bpy.ops.object.mode_set(mode='EDIT')
    arm = obj.data

    bones = {}

    bone = arm.edit_bones.new('Bone')
    bone.head[:] = 0.0000, 0.0000, 0.0000
    bone.tail[:] = 0.0000, 0.0000, 0.3333
    bone.roll = 0.0000
    bone.use_connect = False
    bones['Bone'] = bone.name

    bone = arm.edit_bones.new('Bone.002')
    bone.head[:] = 0.0000, 0.0000, 0.3333
    bone.tail[:] = 0.0000, 0.0000, 0.6667
    bone.roll = 0.0000
    bone.use_connect = True
    bone.parent = arm.edit_bones[bones['Bone']]
    bones['Bone.002'] = bone.name

    bone = arm.edit_bones.new('Bone.001')
    bone.head[:] = 0.0000, 0.0000, 0.6667
    bone.tail[:] = 0.0000, 0.0000, 1.0000
    bone.roll = 0.0000
    bone.use_connect = True
    bone.parent = arm.edit_bones[bones['Bone.002']]
    bones['Bone.001'] = bone.name

    bpy.ops.object.mode_set(mode='OBJECT')
    pbone = obj.pose.bones[bones['Bone']]
    pbone.rigify_type = 'limbs.simple_tentacle'
    pbone.lock_location = (False, False, False)
    pbone.lock_rotation = (False, False, False)
    pbone.lock_rotation_w = False
    pbone.lock_scale = (False, False, False)
    pbone.rotation_mode = 'QUATERNION'
    pbone = obj.pose.bones[bones['Bone.002']]
    pbone.rigify_type = ''
    pbone.lock_location = (False, False, False)
    pbone.lock_rotation = (False, False, False)
    pbone.lock_rotation_w = False
    pbone.lock_scale = (False, False, False)
    pbone.rotation_mode = 'QUATERNION'
    pbone = obj.pose.bones[bones['Bone.001']]
    pbone.rigify_type = ''
    pbone.lock_location = (False, False, False)
    pbone.lock_rotation = (False, False, False)
    pbone.lock_rotation_w = False
    pbone.lock_scale = (False, False, False)
    pbone.rotation_mode = 'QUATERNION'

    bpy.ops.object.mode_set(mode='EDIT')
    for bone in arm.edit_bones:
        bone.select = False
        bone.select_head = False
        bone.select_tail = False
    for b in bones:
        bone = arm.edit_bones[bones[b]]
        bone.select = True
        bone.select_head = True
        bone.select_tail = True
        arm.edit_bones.active = bone
