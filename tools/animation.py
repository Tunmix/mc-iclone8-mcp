import math

import RLPy
from tools.avatar import _avatar
from tools.common import frame_time, require_success


_TRANSITIONS = {
    "linear": RLPy.ETransitionType_Linear,
    "step": RLPy.ETransitionType_Step,
    "ease_in": RLPy.ETransitionType_Ease_In,
    "ease_out": RLPy.ETransitionType_Ease_Out,
    "ease_in_out": RLPy.ETransitionType_Ease_In_Out,
}
_CHANNELS = (("rotation_degrees", "Rotation/Rotation", math.radians), ("position", "Position/Position", float))


def skeleton_of(avatar_name):
    avatar = _avatar(avatar_name)
    skeleton = avatar.GetSkeletonComponent()
    if skeleton is None:
        raise RuntimeError("Avatar has no skeleton component")
    return avatar, skeleton


def find_bone(skeleton, name):
    for bone in skeleton.GetSkinBones():
        if bone.GetName() == name:
            return bone
    raise ValueError("Bone not found: %s (list names with get_skin_bones)" % name)


def clip_at(skeleton, time, clip_index=None):
    if clip_index is not None:
        if clip_index < 0 or clip_index >= skeleton.GetClipCount():
            raise ValueError("clip_index is outside the available clips")
        return skeleton.GetClip(clip_index)
    clip = skeleton.GetClipByTime(time)
    if clip is None:
        raise ValueError("No motion clip at this frame; create one with create_motion_clip")
    return clip


def refresh(avatar):
    # Layer keys only reach the viewport and bone transforms after an explicit update.
    RLPy.RGlobal.ObjectModified(avatar, RLPy.EObjectModifiedType_Motion)
    avatar.Update()


def _rotation_degrees(transform):
    # Same convention as the IK tools and set_bone_keys: applied X, then Y, then Z.
    from tools.ik import quat_to_euler
    q = transform.R()
    return {axis: math.degrees(value) for axis, value in zip("xyz", quat_to_euler([q.X(), q.Y(), q.Z(), q.W()]))}


def _vector(value):
    return {"x": value.x, "y": value.y, "z": value.z}


def get_bone_transforms(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    if "frame" in args:
        RLPy.RGlobal.SetTime(frame_time(args["frame"]))
    avatar.Update()
    wanted = set(args.get("bones") or [])
    bones = []
    for bone in skeleton.GetSkinBones():
        if wanted and bone.GetName() not in wanted:
            continue
        local, world = bone.LocalTransform(), bone.WorldTransform()
        parent = bone.GetParent()
        bones.append({
            "name": bone.GetName(),
            "parent": parent.GetName() if parent is not None else None,
            "local_position": _vector(local.T()),
            "local_rotation_degrees": _rotation_degrees(local),
            "world_position": _vector(world.T()),
            "world_rotation_degrees": _rotation_degrees(world),
        })
    return {"avatar": avatar.GetName(), "frame": args.get("frame"), "bones": bones}


def create_motion_clip(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    length = int(args["length_frames"])
    if length <= 0:
        raise ValueError("length_frames must be greater than zero")
    clip = skeleton.AddClip(frame_time(args["start_frame"]))
    if clip is None:
        raise RuntimeError("iClone could not add a clip (another clip may already cover start_frame)")
    require_success(clip.SetLength(frame_time(length)), "iClone could not set the clip length")
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "start_frame": args["start_frame"], "length_frames": length, "clip_count": skeleton.GetClipCount()}


def _layer_block(clip, skeleton, bone_name):
    control = clip.GetControl("Layer", find_bone(skeleton, bone_name))
    if control is None:
        raise RuntimeError("Bone %s has no motion layer control" % bone_name)
    return control.GetDataBlock()


def set_bone_keys(args):
    """Write rotation/position offsets on the motion layer of the clip under each key's frame."""
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    transition = args.get("transition")
    if transition is not None and transition not in _TRANSITIONS:
        raise ValueError("Unsupported transition: %s" % transition)
    written = 0
    for key in args["keys"]:
        time = frame_time(key["frame"])
        clip = clip_at(skeleton, time, args.get("clip_index"))
        # Layer data blocks are keyed in clip time, not scene time.
        clip_time = clip.SceneTimeToClipTime(time)
        for bone_name, values in key["bones"].items():
            block = _layer_block(clip, skeleton, bone_name)
            for field, prefix, convert in _CHANNELS:
                for axis, value in (values.get(field) or {}).items():
                    if axis not in ("x", "y", "z"):
                        raise ValueError("Unknown axis %s on %s" % (axis, bone_name))
                    channel = prefix + axis.upper()
                    require_success(block.SetData(channel, clip_time, RLPy.RVariant(convert(value))), "iClone could not key %s %s (bones without a %s layer channel cannot take it)" % (bone_name, channel, prefix.split("/")[0]))
                    if transition is not None:
                        block.GetControl(channel).SetKeyTransition(clip_time, _TRANSITIONS[transition], float(args.get("transition_strength", 50.0)))
            written += 1
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "bone_keys": written}


def delete_bone_keys(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    time = frame_time(args["frame"])
    clip = clip_at(skeleton, time, args.get("clip_index"))
    clip_time = clip.SceneTimeToClipTime(time)
    names = args.get("bones") or [bone.GetName() for bone in skeleton.GetSkinBones()]
    removed = []
    for name in names:
        block = _layer_block(clip, skeleton, name)
        hit = False
        for _field, prefix, _convert in _CHANNELS:
            for axis in "XYZ":
                control = block.GetControl(prefix + axis)
                if control is not None and control.RemoveKey(clip_time) == RLPy.RStatus.Success:
                    hit = True
        if hit:
            removed.append(name)
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "frame": args["frame"], "removed": removed}


def _clip_by_index(args, field="clip_index"):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    index = args[field]
    if index < 0 or index >= skeleton.GetClipCount():
        raise ValueError("%s is outside the available clips" % field)
    return avatar, skeleton, skeleton.GetClip(index)


def _clip_operation(operation, message):
    def handler(args):
        avatar, skeleton, clip = _clip_by_index(args)
        require_success(getattr(skeleton, operation)(clip), message)
        refresh(avatar)
        return {"status": "ok", "avatar": avatar.GetName(), "clip_index": args["clip_index"], "clip_count": skeleton.GetClipCount()}
    return handler


def break_clip(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    require_success(skeleton.BreakClip(frame_time(args["frame"])), "iClone could not break the clip at frame %s" % args["frame"])
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "frame": args["frame"], "clip_count": skeleton.GetClipCount()}


def merge_clips(args):
    avatar, skeleton, first = _clip_by_index(args, "first_clip_index")
    second = _clip_by_index(args, "second_clip_index")[2]
    require_success(skeleton.MergeClips(first, second), "iClone could not merge the clips")
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "clip_count": skeleton.GetClipCount()}


def delete_clip(args):
    if args.get("confirm") != "DELETE_CLIP":
        raise ValueError("confirm must be DELETE_CLIP")
    return _clip_operation("DeleteClip", "iClone could not delete the clip")(args)


def bake_fk_to_ik(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    require_success(skeleton.BakeFkToIk(frame_time(args.get("frame", 0)), bool(args.get("all_clips", True))), "iClone could not bake FK to IK")
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName()}


def register(registry):
    avatar = {"avatar_name": {"type": "string"}}
    clip_schema = {"type": "object", "properties": dict(avatar, clip_index={"type": "integer", "minimum": 0}), "required": ["clip_index"]}
    axes = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}}
    registry["get_bone_transforms"] = {"handler": get_bone_transforms, "main_thread": True, "description": "Reads skin bone local/world position and rotation (degrees, applied X then Y then Z) at a frame. Moves the timeline to that frame. Use it to check a pose, e.g. where a hand is.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0}, bones={"type": "array", "items": {"type": "string"}})}}
    registry["create_motion_clip"] = {"handler": create_motion_clip, "main_thread": True, "description": "Adds an empty motion clip to an avatar so bone and IK keys can be written into it.", "inputSchema": {"type": "object", "properties": dict(avatar, start_frame={"type": "integer", "minimum": 0}, length_frames={"type": "integer", "minimum": 1}), "required": ["start_frame", "length_frames"]}}
    registry["set_bone_keys"] = {"handler": set_bone_keys, "main_thread": True, "description": "Keys skin bones on the motion layer of the clip under each frame. Values are offsets on top of the clip's pose: rotation_degrees in the bone's local axes, position in cm. Batch many frames and bones in one call.", "inputSchema": {"type": "object", "properties": dict(avatar, clip_index={"type": "integer", "minimum": 0}, transition={"type": "string", "enum": list(_TRANSITIONS)}, transition_strength={"type": "number", "minimum": 0, "maximum": 100}, keys={"type": "array", "items": {"type": "object", "properties": {"frame": {"type": "integer", "minimum": 0}, "bones": {"type": "object", "additionalProperties": {"type": "object", "properties": {"rotation_degrees": axes, "position": axes}}}}, "required": ["frame", "bones"]}}), "required": ["keys"]}}
    registry["delete_bone_keys"] = {"handler": delete_bone_keys, "main_thread": True, "description": "Removes motion layer keys at a frame, for the listed bones or for all skin bones.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0}, clip_index={"type": "integer", "minimum": 0}, bones={"type": "array", "items": {"type": "string"}}), "required": ["frame"]}}
    registry["mirror_clip"] = {"handler": _clip_operation("MirrorClip", "iClone could not mirror the clip"), "main_thread": True, "description": "Mirrors a motion clip left/right, e.g. to turn a left turn into a right turn.", "inputSchema": clip_schema}
    registry["sample_clip"] = {"handler": _clip_operation("SampleMotionClip", "iClone could not sample the clip"), "main_thread": True, "description": "Samples a motion clip into per-frame keys (iClone Sample Motion Clip).", "inputSchema": clip_schema}
    registry["flatten_clip"] = {"handler": _clip_operation("FlattenMotionClip", "iClone could not flatten the clip"), "main_thread": True, "description": "Bakes the motion layer edits into the clip itself (iClone Flatten Motion Clip).", "inputSchema": clip_schema}
    registry["break_clip"] = {"handler": break_clip, "main_thread": True, "description": "Splits the motion clip under a frame into two clips.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0}), "required": ["frame"]}}
    registry["merge_clips"] = {"handler": merge_clips, "main_thread": True, "description": "Merges two motion clips of an avatar into one.", "inputSchema": {"type": "object", "properties": dict(avatar, first_clip_index={"type": "integer", "minimum": 0}, second_clip_index={"type": "integer", "minimum": 0}), "required": ["first_clip_index", "second_clip_index"]}}
    registry["delete_clip"] = {"handler": delete_clip, "main_thread": True, "description": "Deletes a motion clip. Destructive action.", "inputSchema": {"type": "object", "properties": dict(avatar, clip_index={"type": "integer", "minimum": 0}, confirm={"type": "string", "enum": ["DELETE_CLIP"]}), "required": ["clip_index", "confirm"]}}
    registry["bake_fk_to_ik"] = {"handler": bake_fk_to_ik, "main_thread": True, "description": "Bakes FK bone keys into IK effector keys, from a frame or for all clips.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0}, all_clips={"type": "boolean"})}}
