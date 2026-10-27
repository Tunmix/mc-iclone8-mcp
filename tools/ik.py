import math

import RLPy
from tools.animation import clip_at, find_bone, refresh, skeleton_of
from tools.common import frame_time, require_success
from tools.objects import find_by_name


EFFECTORS = {
    "hip": RLPy.EHikEffector_Hip,
    "chest": RLPy.EHikEffector_ChestOrigin,
    "neck": RLPy.EHikEffector_Neck,
    "head": RLPy.EHikEffector_Head,
    "left_shoulder": RLPy.EHikEffector_LeftShoulder,
    "right_shoulder": RLPy.EHikEffector_RightShoulder,
    "left_elbow": RLPy.EHikEffector_LeftElbow,
    "right_elbow": RLPy.EHikEffector_RightElbow,
    "left_hand": RLPy.EHikEffector_LeftHand,
    "right_hand": RLPy.EHikEffector_RightHand,
    "left_hip": RLPy.EHikEffector_LeftHip,
    "right_hip": RLPy.EHikEffector_RightHip,
    "left_knee": RLPy.EHikEffector_LeftKnee,
    "right_knee": RLPy.EHikEffector_RightKnee,
    "left_foot": RLPy.EHikEffector_LeftFoot,
    "right_foot": RLPy.EHikEffector_RightFoot,
    "left_toe": RLPy.EHikEffector_LeftToe,
    "right_toe": RLPy.EHikEffector_RightToe,
}

# Limb chains as HumanIK effectors: upper, mid and end bone.
_CHAINS = {
    "left_hand": ("left_shoulder", "left_elbow", "left_hand"),
    "right_hand": ("right_shoulder", "right_elbow", "right_hand"),
    "left_foot": ("left_hip", "left_knee", "left_foot"),
    "right_foot": ("right_hip", "right_knee", "right_foot"),
}

# Quaternions are [x, y, z, w] (Hamilton). Measured against iClone 8: world = parent_world * local,
# and a motion layer rotation (rx, ry, rz) composes as local = Rz * Ry * Rx * base.


def _qmul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return [aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz]


def _qinv(q):
    return [-q[0], -q[1], -q[2], q[3]]


def _qaxis(axis, radians):
    half = radians / 2.0
    q = [0.0, 0.0, 0.0, math.cos(half)]
    q["xyz".index(axis)] = math.sin(half)
    return q


def euler_to_quat(rx, ry, rz):
    return _qmul(_qmul(_qaxis("z", rz), _qaxis("y", ry)), _qaxis("x", rx))


def quat_to_euler(q):
    x, y, z, w = q
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    r10 = 2 * (x * y + z * w)
    r00 = 1 - 2 * (y * y + z * z)
    return math.atan2(r21, r22), math.asin(max(-1.0, min(1.0, -r20))), math.atan2(r10, r00)


def _rotate(q, v):
    return _qmul(_qmul(q, [v[0], v[1], v[2], 0.0]), _qinv(q))[:3]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _scale(a, factor):
    return [a[0] * factor, a[1] * factor, a[2] * factor]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _length(a):
    return math.sqrt(_dot(a, a))


def _normalize(a):
    length = _length(a)
    return _scale(a, 1.0 / length) if length > 1e-9 else [0.0, 0.0, 0.0]


def _q_between(u, v):
    """Shortest rotation taking direction u onto direction v."""
    u, v = _normalize(u), _normalize(v)
    d = _dot(u, v)
    if d < -0.999999:
        axis = _cross([1.0, 0.0, 0.0], u)
        if _length(axis) < 1e-6:
            axis = _cross([0.0, 1.0, 0.0], u)
        axis = _normalize(axis)
        return [axis[0], axis[1], axis[2], 0.0]
    c = _cross(u, v)
    q = [c[0], c[1], c[2], 1.0 + d]
    norm = math.sqrt(sum(value * value for value in q))
    return [value / norm for value in q]


def _from_rl(q):
    return [q.X(), q.Y(), q.Z(), q.W()]


def _position(bone):
    value = bone.WorldTransform().T()
    return [value.x, value.y, value.z]


def _world_rotation(bone):
    return _from_rl(bone.WorldTransform().R())


def _vector(value, default):
    return [float(value.get(axis, default[index])) for index, axis in enumerate("xyz")]


def _euler_target(value):
    return euler_to_quat(*[math.radians(float(value.get(axis, 0.0))) for axis in "xyz"])


def _layer_rotation(block, clip_time):
    return euler_to_quat(*[block.GetData("Rotation/Rotation" + axis, clip_time).ToFloat() for axis in "XYZ"])


def _key_world_rotation(clip, clip_time, bone, parent_world, world):
    """Key the motion layer so the bone ends up with this world rotation under parent_world."""
    desired = _qmul(_qinv(parent_world), world)
    block = clip.GetControl("Layer", bone).GetDataBlock()
    base = _qmul(_qinv(_layer_rotation(block, clip_time)), _from_rl(bone.LocalTransform().R()))
    for axis, value in zip("XYZ", quat_to_euler(_qmul(desired, _qinv(base)))):
        require_success(block.SetData("Rotation/Rotation" + axis, clip_time, RLPy.RVariant(value)), "iClone could not key %s" % bone.GetName())


def _key_world_position(clip, clip_time, bone, world_position):
    """Position layer offsets are added to the bone's position in its parent's space."""
    parent = bone.GetParent().WorldTransform()
    origin = parent.T()
    local = _rotate(_qinv(_from_rl(parent.R())), _sub(world_position, [origin.x, origin.y, origin.z]))
    current = bone.LocalTransform().T()
    current = [current.x, current.y, current.z]
    block = clip.GetControl("Layer", bone).GetDataBlock()
    for index, axis in enumerate("XYZ"):
        channel = "Position/Position" + axis
        offset = block.GetData(channel, clip_time).ToFloat()
        require_success(block.SetData(channel, clip_time, RLPy.RVariant(local[index] - (current[index] - offset))), "iClone could not key %s position" % bone.GetName())


def _forward(avatar):
    # CC characters face -Y in their own space.
    return _rotate(_from_rl(avatar.WorldTransform().R()), [0.0, -1.0, 0.0])


def _two_bone(clip, clip_time, avatar, chain, goal, end_rotation, pole, is_leg):
    """Analytic two-bone IK on the evaluated pose; the end bone keeps (or takes) a world rotation."""
    upper, mid, end = chain
    a_pos, b_pos, c_pos = _position(upper), _position(mid), _position(end)
    upper_rotation, mid_rotation = _world_rotation(upper), _world_rotation(mid)
    end_rotation = end_rotation or _world_rotation(end)
    a, b = _length(_sub(b_pos, a_pos)), _length(_sub(c_pos, b_pos))
    to_goal = _sub(goal, a_pos)
    distance = min(max(_length(to_goal), abs(a - b) + 1e-3), a + b - 1e-3)
    u = _normalize(to_goal)
    hint = _sub(pole, a_pos) if pole else _sub(b_pos, a_pos)
    hint = _sub(hint, _scale(u, _dot(hint, u)))
    if _length(hint) < 1e-3 * a:
        # Straight limb with no pole: knees bend forward, elbows down and back.
        forward = _forward(avatar)
        hint = forward if is_leg else _add([0.0, 0.0, -1.0], _scale(forward, -0.3))
        hint = _sub(hint, _scale(u, _dot(hint, u)))
    v = _normalize(hint)
    cos_angle = max(-1.0, min(1.0, (a * a + distance * distance - b * b) / (2 * a * distance)))
    mid_goal = _add(a_pos, _scale(_add(_scale(u, cos_angle), _scale(v, math.sqrt(1 - cos_angle * cos_angle))), a))
    end_goal = _add(a_pos, _scale(u, distance))
    upper_delta = _q_between(_sub(b_pos, a_pos), _sub(mid_goal, a_pos))
    new_upper = _qmul(upper_delta, upper_rotation)
    mid_delta = _q_between(_rotate(upper_delta, _sub(c_pos, b_pos)), _sub(end_goal, mid_goal))
    new_mid = _qmul(_qmul(mid_delta, upper_delta), mid_rotation)
    _key_world_rotation(clip, clip_time, upper, _world_rotation(upper.GetParent()), new_upper)
    _key_world_rotation(clip, clip_time, mid, new_upper, new_mid)
    _key_world_rotation(clip, clip_time, end, new_mid, end_rotation)
    return [upper.GetName(), mid.GetName(), end.GetName()]


def _bone_of(hik, name):
    bone = hik.GetBone(EFFECTORS[name])
    if bone is None:
        raise RuntimeError("Avatar has no bone for the %s effector" % name)
    return bone


def set_ik_pose(args):
    """Place the hip and limb ends at world targets on the current pose and key the result as bone keys."""
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    hik = avatar.GetHikEffectorComponent()
    if hik is None:
        raise RuntimeError("Avatar has no HumanIK effector component")
    effectors = args.get("effectors") or {}
    unknown = [name for name in effectors if name != "hip" and name not in _CHAINS]
    if not effectors or unknown:
        raise ValueError("effectors must be some of: hip, %s" % ", ".join(_CHAINS))
    time = frame_time(args["frame"])
    clip = clip_at(skeleton, time, args.get("clip_index"))
    clip_time = clip.SceneTimeToClipTime(time)
    RLPy.RGlobal.SetTime(time)
    avatar.Update()
    goals = {}
    for name, target in effectors.items():
        if name == "hip":
            continue
        end = _bone_of(hik, _CHAINS[name][2])
        rotation = _euler_target(target["rotation_degrees"]) if target.get("rotation_degrees") is not None else None
        pole = _vector(target["pole"], [0.0, 0.0, 0.0]) if target.get("pole") else None
        goals[name] = (_vector(target.get("position") or {}, _position(end)), rotation, pole)
    keyed = []
    if "hip" in effectors:
        target = effectors["hip"]
        hip = _bone_of(hik, "hip")
        if target.get("keep_feet", True):
            # Planted feet keep their world position and rotation unless the call moves them too.
            for name in ("left_foot", "right_foot"):
                if name not in goals:
                    foot = _bone_of(hik, _CHAINS[name][2])
                    goals[name] = (_position(foot), _world_rotation(foot), None)
        if target.get("position"):
            _key_world_position(clip, clip_time, hip, _vector(target["position"], _position(hip)))
        if target.get("rotation_degrees") is not None:
            _key_world_rotation(clip, clip_time, hip, _world_rotation(hip.GetParent()), _euler_target(target["rotation_degrees"]))
        keyed.append(hip.GetName())
        refresh(avatar)
    for name, (goal, rotation, pole) in goals.items():
        chain = [_bone_of(hik, effector) for effector in _CHAINS[name]]
        keyed.extend(_two_bone(clip, clip_time, avatar, chain, goal, rotation, pole, name.endswith("foot")))
        refresh(avatar)
    reached = {}
    for name in effectors:
        position = _position(_bone_of(hik, "hip" if name == "hip" else _CHAINS[name][2]))
        reached[name] = {"x": position[0], "y": position[1], "z": position[2]}
    return {"status": "ok", "avatar": avatar.GetName(), "frame": args["frame"], "keyed_bones": keyed, "effector_positions": reached}


def get_ik_effectors(args):
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    hik = avatar.GetHikEffectorComponent()
    if hik is None:
        raise RuntimeError("Avatar has no HumanIK effector component")
    if "frame" in args:
        RLPy.RGlobal.SetTime(frame_time(args["frame"]))
    avatar.Update()
    effectors = {}
    for name, effector in EFFECTORS.items():
        bone = hik.GetBone(effector)
        if bone is None:
            continue
        rx, ry, rz = quat_to_euler(_world_rotation(bone))
        position = _position(bone)
        effectors[name] = {
            "bone": bone.GetName(),
            "position": {"x": position[0], "y": position[1], "z": position[2]},
            "rotation_degrees": {"x": math.degrees(rx), "y": math.degrees(ry), "z": math.degrees(rz)},
        }
    forward = _forward(avatar)
    return {"avatar": avatar.GetName(), "frame": args.get("frame"), "facing": {"x": forward[0], "y": forward[1], "z": forward[2]}, "effectors": effectors}


_REACH_TYPES = {"target": RLPy.ReachKeyType_Target, "lock": RLPy.ReachKeyType_Lock, "release": RLPy.ReachKeyType_Release}


def add_reach_key(args):
    """HumanIK reach: from a frame the hand/foot goes to an object (target), stays where it is (lock) or lets go (release)."""
    avatar, _skeleton = skeleton_of(args.get("avatar_name"))
    hik = avatar.GetHikEffectorComponent()
    if hik is None:
        raise RuntimeError("Avatar has no HumanIK effector component")
    effector = args["effector"]
    if effector not in _CHAINS:
        raise ValueError("effector must be one of %s" % ", ".join(_CHAINS))
    mode = args.get("mode", "target")
    if mode not in _REACH_TYPES:
        raise ValueError("mode must be target, lock or release")
    target = find_by_name(args["target_name"]) if mode == "target" else None
    if mode == "target" and target is None:
        raise ValueError("target_name is required for mode target")
    key = RLPy.RReachKey(frame_time(args["frame"]), bool(args.get("pull", False)), bool(args.get("rotate", True)), frame_time(int(args.get("transition_frames", 10))), target, _REACH_TYPES[mode])
    require_success(hik.AddReachKey(EFFECTORS[effector], key), "iClone could not add the reach key")
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "effector": effector, "frame": args["frame"], "mode": mode, "reach_keys": len(hik.GetReachKeys(EFFECTORS[effector]))}


def add_look_at_key(args):
    """From a frame the avatar's head and eyes turn to follow an object."""
    avatar, skeleton = skeleton_of(args.get("avatar_name"))
    look_at = skeleton.GetLookAtComponent()
    if look_at is None:
        raise RuntimeError("Avatar has no look-at component")
    target = find_by_name(args["target_name"])
    require_success(look_at.AddLookAtKey(frame_time(args["frame"]), target), "iClone could not add the look-at key")
    refresh(avatar)
    return {"status": "ok", "avatar": avatar.GetName(), "target": target.GetName(), "frame": args["frame"]}


def register(registry):
    avatar = {"avatar_name": {"type": "string"}}
    axes = {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}}}
    target = {"type": "object", "properties": {"position": axes, "rotation_degrees": axes, "pole": axes, "keep_feet": {"type": "boolean"}}}
    registry["get_ik_effectors"] = {"handler": get_ik_effectors, "main_thread": True, "description": "Lists IK effector bones (hip, hands, feet, knees, elbows, head...) with world position (cm, Z up) and rotation (degrees, applied X then Y then Z) at a frame, plus the avatar's facing direction. Start set_ik_pose targets from these values.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0})}}
    registry["set_ik_pose"] = {"handler": set_ik_pose, "main_thread": True, "description": "IK posing at a frame, keyed as motion layer bone keys on top of the current pose (needs a clip under the frame). Effectors: hip, left_hand, right_hand, left_foot, right_foot. position is a world target (omitted axes keep the current value); rotation_degrees sets the end bone's world rotation (default: keep it); pole is a world point the elbow/knee bends toward. Moving the hip keeps both feet planted unless keep_feet is false, e.g. lower hip z for a crouch. Returns where each effector ended up.", "inputSchema": {"type": "object", "properties": dict(avatar, frame={"type": "integer", "minimum": 0}, clip_index={"type": "integer", "minimum": 0}, effectors={"type": "object", "properties": {name: target for name in ("hip",) + tuple(_CHAINS)}, "additionalProperties": False}), "required": ["frame", "effectors"]}}
    registry["add_reach_key"] = {"handler": add_reach_key, "main_thread": True, "description": "HumanIK reach key: from frame, a hand or foot reaches an object (mode target), stays locked where it is (lock) or lets go (release), blending over transition_frames. Good for a hand on a barrier or car hood. Check the result with capture_viewport.", "inputSchema": {"type": "object", "properties": dict(avatar, effector={"type": "string", "enum": list(_CHAINS)}, target_name={"type": "string"}, frame={"type": "integer", "minimum": 0}, mode={"type": "string", "enum": list(_REACH_TYPES)}, transition_frames={"type": "integer", "minimum": 0}, rotate={"type": "boolean", "description": "Also match the target's rotation"}, pull={"type": "boolean", "description": "Let the body be pulled toward the target"}), "required": ["effector", "frame"]}}
    registry["add_look_at_key"] = {"handler": add_look_at_key, "main_thread": True, "description": "From a frame, the avatar's head and eyes follow an object (e.g. a dummy prop where the patient's camera is).", "inputSchema": {"type": "object", "properties": dict(avatar, target_name={"type": "string"}, frame={"type": "integer", "minimum": 0}), "required": ["target_name", "frame"]}}
