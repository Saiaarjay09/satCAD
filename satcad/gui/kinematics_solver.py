"""Kinematic joints and the chain solver for Motion Studio. Pure NumPy: no Qt, no CAD kernel, so it is fast and easy to test.

A JOINT is bound to an ASSEMBLY of the design. Every part inside that assembly (including nested sub-assemblies) moves rigidly with the joint.
All joint axes and pivots are given in the model's HOME pose (the pose the part has when nothing is moving) in world coordinates, millimetres.

JOINT TYPES
    revolute   one rotation about `axis` through `pivot`            (hinge, drive, deployment)
    prismatic  one translation along `axis`                          (slider, extension)
    spherical  two rotations: first about `axis` at `pivot`, then about `axis2` at `pivot2`   (gimbal, mirror tip / tilt)

NESTING: an assembly inside another assembly that also has a joint is carried by it (a camera on a pan-tilt head). The solver composes
the parents' motion automatically: world = parent_world x joint_local.

HOW THE 3D VIEW STAYS FAST: the solver only produces 4x4 matrices. The viewport applies them to the EXISTING mesh actors (a per-actor
transform), so nothing is re-tessellated or rebuilt while an animation plays: a frame costs a few matrix multiplications.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

JOINT_KINDS = ("revolute", "prismatic", "spherical")


class KinematicsError(ValueError):
    """A problem with a joint definition, in plain words."""


def _unit(v, what: str = "axis") -> np.ndarray:
    a = np.asarray(v, dtype=float).reshape(-1)
    if a.shape != (3,) or not np.all(np.isfinite(a)):
        raise KinematicsError(f"The {what} must be three numbers, for example (0, 0, 1).")
    n = np.linalg.norm(a)
    if n < 1e-12:
        raise KinematicsError(f"The {what} cannot be the zero vector.")
    return a / n


def rotation_matrix(axis, degrees: float) -> np.ndarray:
    """3x3 rotation about a unit axis through the origin (Rodrigues' formula)."""
    k = _unit(axis)
    a = np.radians(degrees)
    c, s = np.cos(a), np.sin(a)
    K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) * c + s * K + (1 - c) * np.outer(k, k)


def rotation_about(axis, pivot, degrees: float) -> np.ndarray:
    """4x4 matrix: rotate by `degrees` about the line through `pivot` along `axis`."""
    m = np.eye(4)
    p = np.asarray(pivot, dtype=float)
    r = rotation_matrix(axis, degrees)
    m[:3, :3] = r
    m[:3, 3] = p - r @ p
    return m


def translation_matrix(axis, distance: float) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = _unit(axis) * distance
    return m


@dataclass
class Joint:
    name: str
    kind: str
    assembly: str                                   # the assembly (a feature name in the design) whose parts move
    axis: tuple = (0.0, 0.0, 1.0)
    pivot: tuple = (0.0, 0.0, 0.0)
    expr: str = ""                                  # name of the function driving the first degree of freedom (degrees, or mm for prismatic)
    axis2: tuple = (1.0, 0.0, 0.0)                  # spherical joints: second rotation axis
    pivot2: tuple | None = None                     # ... and its pivot (defaults to `pivot`)
    expr2: str = ""                                 # ... and the function driving it
    offset: float = 0.0                             # added to the driven value: e.g. -90 when the model is drawn deployed and 0 means stowed
    offset2: float = 0.0
    minimum: float | None = None                    # optional clamp of the driven value (limits of travel)
    maximum: float | None = None

    def validate(self) -> None:
        if self.kind not in JOINT_KINDS:
            raise KinematicsError(f"Joint type must be one of {', '.join(JOINT_KINDS)}.")
        if not self.name or not self.assembly:
            raise KinematicsError("A joint needs a name and the assembly it moves.")
        _unit(self.axis)
        if self.kind == "spherical":
            _unit(self.axis2, "second axis")
        for v in (self.pivot, self.pivot2 or self.pivot):
            if np.asarray(v, dtype=float).shape != (3,):
                raise KinematicsError("A pivot must be three numbers (x, y, z) in mm.")

    @property
    def drivers(self) -> list[str]:
        """The function names this joint reads."""
        return [e for e in (self.expr, self.expr2 if self.kind == "spherical" else "") if e]

    def _clamp(self, v: float) -> float:
        if self.minimum is not None:
            v = max(self.minimum, v)
        if self.maximum is not None:
            v = min(self.maximum, v)
        return v

    def local_matrix(self, v1: float = 0.0, v2: float = 0.0) -> np.ndarray:
        """The joint's own motion in the home frame, for driven values v1 (and v2 for spherical joints)."""
        v1 = self._clamp(float(v1) + self.offset)
        if self.kind == "revolute":
            return rotation_about(self.axis, self.pivot, v1)
        if self.kind == "prismatic":
            return translation_matrix(self.axis, v1)
        v2 = float(v2) + self.offset2
        second = self.pivot2 if self.pivot2 is not None else self.pivot
        return rotation_about(self.axis, self.pivot, v1) @ rotation_about(self.axis2, second, v2)

    def to_dict(self) -> dict:
        d = {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.__dict__.items()}
        return d

    @staticmethod
    def from_dict(d: dict) -> "Joint":
        known = {k: d[k] for k in Joint.__dataclass_fields__ if k in d}
        for k in ("axis", "pivot", "axis2", "pivot2"):
            if known.get(k) is not None:
                known[k] = tuple(known[k])
        return Joint(**known)


@dataclass
class ColorPulse:
    """Colours a row of parts with a travelling wave: part k gets intensity function(t, k), blended from `base` to `hot` (RGB 0..1)."""
    name: str
    parts: list
    expr: str                                       # a function of (t, k) returning 0..1
    base: tuple = (0.75, 0.75, 0.78)
    hot: tuple = (1.0, 0.45, 0.15)

    def colours(self, intensity: np.ndarray) -> dict:
        x = np.clip(np.asarray(intensity, dtype=float), 0.0, 1.0)
        b, h = np.asarray(self.base), np.asarray(self.hot)
        return {p: tuple(b + (h - b) * x[i]) for i, p in enumerate(self.parts)}

    def to_dict(self) -> dict:
        return dict(name=self.name, parts=list(self.parts), expr=self.expr, base=list(self.base), hot=list(self.hot))

    @staticmethod
    def from_dict(d: dict) -> "ColorPulse":
        return ColorPulse(d["name"], list(d["parts"]), d["expr"], tuple(d.get("base", (0.75, 0.75, 0.78))), tuple(d.get("hot", (1.0, 0.45, 0.15))))


def assembly_ancestors(design) -> dict[str, list[str]]:
    """For every assembly: its chain of assemblies from itself up to the root, e.g. turret_az -> [turret_az, turret]."""
    parent = {n: f.raw.get("parent") for n, f in design.features.items() if f.kind == "assembly"}
    chains = {}
    for n in parent:
        chain, seen = [], set()
        while n and n in parent and n not in seen:
            chain.append(n)
            seen.add(n)
            n = parent[n]
        chains[chain[0]] = chain
    return chains


class KinematicsSolver:
    """Holds the joints and turns driven values into world transforms for every moving part."""

    def __init__(self, joints=(), design=None):
        self.joints: list[Joint] = []
        self._parent: dict[str, str | None] = {}
        self._part_joint: dict[str, str] = {}
        self._order: list[str] = []
        self.set_joints(list(joints), design)

    # ---- structure -------------------------------------------------------------------------------------------------------------------
    def set_joints(self, joints: list[Joint], design=None) -> None:
        names = [j.name for j in joints]
        if len(set(names)) != len(names):
            raise KinematicsError("Two joints have the same name.")
        for j in joints:
            j.validate()
        chains = assembly_ancestors(design) if design is not None else {}
        asm_to_joint = {}
        for j in joints:
            if design is not None and j.assembly not in chains:
                raise KinematicsError(f"Joint '{j.name}': there is no assembly called '{j.assembly}' in this part.")
            if j.assembly in asm_to_joint:
                raise KinematicsError(f"Assembly '{j.assembly}' already has the joint '{asm_to_joint[j.assembly]}'. Give each assembly one joint "
                                      "(use a spherical joint for two rotations, or nest an inner assembly for a second stage).")
            asm_to_joint[j.assembly] = j.name
        parent: dict[str, str | None] = {}
        for j in joints:                                  # the nearest enclosing assembly that also has a joint carries this one
            parent[j.name] = None
            for anc in chains.get(j.assembly, [j.assembly])[1:]:
                if anc in asm_to_joint:
                    parent[j.name] = asm_to_joint[anc]
                    break
        part_joint: dict[str, str] = {}
        if design is not None:
            for pname, part in design.parts.items():
                for anc in chains.get(part.assembly, [])[:] if part.assembly else []:
                    if anc in asm_to_joint:
                        part_joint[pname] = asm_to_joint[anc]
                        break
        order, placed = [], set()
        while len(order) < len(joints):                   # parents before children
            progressed = False
            for j in joints:
                if j.name not in placed and (parent[j.name] is None or parent[j.name] in placed):
                    order.append(j.name)
                    placed.add(j.name)
                    progressed = True
            if not progressed:
                raise KinematicsError("The joints form a loop. A joint cannot (indirectly) carry itself.")
        self.joints, self._parent, self._part_joint, self._order = joints, parent, part_joint, order

    def joint(self, name: str) -> Joint:
        for j in self.joints:
            if j.name == name:
                return j
        raise KinematicsError(f"No joint called '{name}'.")

    def parts_of(self, joint_name: str) -> list[str]:
        return [p for p, j in self._part_joint.items() if j == joint_name]

    @property
    def moving_parts(self) -> list[str]:
        return list(self._part_joint)

    # ---- solving ---------------------------------------------------------------------------------------------------------------------
    def joint_matrices(self, values: dict[str, tuple]) -> dict[str, np.ndarray]:
        """World matrix of every joint. `values[name]` is (v1,) or (v1, v2); missing joints stay at their home value (0)."""
        world: dict[str, np.ndarray] = {}
        by_name = {j.name: j for j in self.joints}
        for n in self._order:
            v = tuple(values.get(n, (0.0, 0.0))) + (0.0, 0.0)
            local = by_name[n].local_matrix(v[0], v[1])
            p = self._parent[n]
            world[n] = local if p is None else world[p] @ local
        return world

    def part_matrices(self, values: dict[str, tuple]) -> dict[str, np.ndarray]:
        """World matrix for every moving part: the matrix of the deepest joint that carries it."""
        world = self.joint_matrices(values)
        return {p: world[j] for p, j in self._part_joint.items()}

    def pivot_world(self, joint_name: str, values: dict[str, tuple]) -> np.ndarray:
        """Where a joint's pivot is right now (it moves if a parent joint moves it)."""
        world = self.joint_matrices(values)
        p = np.append(np.asarray(self.joint(joint_name).pivot, dtype=float), 1.0)
        parent = self._parent[joint_name]
        m = world[parent] if parent else np.eye(4)
        return (m @ p)[:3]
