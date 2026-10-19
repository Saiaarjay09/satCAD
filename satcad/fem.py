"""
Shell finite-element analysis for thin-walled spacecraft structures (BETA).

WHAT IT IS
  A small, self-contained linear FE solver for plate and shell structures: panels, shear walls, box-like buses.
  Elements are flat 3-node triangles combining
    * a constant-strain membrane (CST),
    * a Mindlin-Reissner plate-bending element with the Discrete Shear Gap (DSG3) shear treatment, which does not lock
      for thin plates, and
    * a small artificial "drilling" stiffness so the in-plane rotation is defined.
  Parts that touch are joined by exact (master-slave) tie constraints. Compact items (equipment, tanks, fasteners) are
  modelled as lumped masses spread over nearby structure.

WHAT IT CAN DO
  modal analysis  : natural frequencies, mode shapes, effective modal mass in X/Y/Z (what launch requirements use)
  static analysis : uniform acceleration (g-load) or pressure: displacements, von Mises stress, margin of safety, reactions

VERIFICATION (see tests/test_fem.py): simply supported and clamped square plates (deflection and fundamental frequency),
cantilever plate frequency, uniaxial membrane tension, rigid-body modes, mass and force equilibrium.

LIMITS (read these before trusting a number)
  * Flat plates only: curved shells, holes, fillets, ribs, joints' local stiffness and bolted-joint slip are NOT modelled.
  * Sandwich panels are idealised as isotropic plates with an effective stiffness; composites as quasi-isotropic.
  * Equipment is lumped (its own rotary inertia and stiffness are neglected).
  * Concept-stage estimates: results must be cross-checked with an established FE tool before they are relied on for flight.

Units: mm, kg-per-mm^3 handled internally as tonnes (N, mm, tonne, s): stress in MPa, force in N, frequency in Hz.
Densities arrive in kg/m^3 and are converted: 1 kg/m^3 = 1e-12 tonne/mm^3.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla

from .materials import get as get_material

G0 = 9806.65                                   # standard gravity in mm/s^2
DOF = 6                                        # ux uy uz thx thy thz per node


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass
class Plate:
    """A flat rectangular plate: mid-surface centre, orthonormal axes (e1, e2 in-plane, e3 normal), size a x b, thickness t."""
    name: str
    centre: np.ndarray
    e1: np.ndarray
    e2: np.ndarray
    a: float
    b: float
    t: float
    E: float                                   # MPa
    nu: float
    rho: float                                 # tonne/mm^3
    yield_mpa: float | None = None
    part: str = ""                             # the design part this plate belongs to
    nx: int | None = None                      # element divisions along e1 / e2 (set by the model if None)
    ny: int | None = None

    @property
    def e3(self) -> np.ndarray:
        return np.cross(self.e1, self.e2)

    @property
    def area(self) -> float:
        return self.a * self.b


@dataclass
class LumpedMass:
    name: str
    mass: float                                # tonne
    at: np.ndarray
    attach_to: list = field(default_factory=list)   # [(part names)] to prefer; empty = nearest structure


# --------------------------------------------------------------------------- #
# Element
# --------------------------------------------------------------------------- #
def _shell_element(X: np.ndarray, E: float, nu: float, t: float, rho: float, kappa: float = 5 / 6, drill: float = 0.0):
    """
    Stiffness (18x18, GLOBAL axes), lumped mass (18) and the local frame of one triangle.
    X: 3x3 node coordinates (rows). DOF order per node: ux uy uz thx thy thz.
    """
    x1, x2, x3 = X
    d12, d13 = x2 - x1, x3 - x1
    nrm = np.cross(d12, d13)
    A = 0.5 * np.linalg.norm(nrm)
    e1 = d12 / np.linalg.norm(d12)
    e3 = nrm / (2 * A)
    e2 = np.cross(e3, e1)
    R = np.array([e1, e2, e3])                                  # local = R @ global
    xl = np.array([[0.0, 0.0], [d12 @ e1, d12 @ e2], [d13 @ e1, d13 @ e2]])
    x, y = xl[:, 0], xl[:, 1]

    b = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]])
    c = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]])
    Nx, Ny = b / (2 * A), c / (2 * A)

    # membrane (CST)
    Bm = np.zeros((3, 6))
    for i in range(3):
        Bm[0, 2 * i], Bm[1, 2 * i + 1] = Nx[i], Ny[i]
        Bm[2, 2 * i], Bm[2, 2 * i + 1] = Ny[i], Nx[i]
    c0 = E / (1 - nu ** 2)
    Dm = t * c0 * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
    Km = A * Bm.T @ Dm @ Bm

    # bending: dofs (w, thx, thy); normal rotation vector beta = (thy, -thx)
    Bb = np.zeros((3, 9))
    for i in range(3):
        Bb[0, 3 * i + 2] = Nx[i]                                # kx  =  d(thy)/dx
        Bb[1, 3 * i + 1] = -Ny[i]                               # ky  = -d(thx)/dy
        Bb[2, 3 * i + 2], Bb[2, 3 * i + 1] = Ny[i], -Nx[i]      # kxy =  d(thy)/dy - d(thx)/dx
    Db = (t ** 3 * c0 / 12) * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
    Kb = A * Bb.T @ Db @ Bb

    # transverse shear: Discrete Shear Gap (DSG3): constant shear strain from the gaps between node 1 and nodes 2, 3
    Bg = np.zeros((2, 9))
    for row, j in enumerate((1, 2)):
        dx, dy = x[j] - x[0], y[j] - y[0]
        Bg[row, 3 * j + 0], Bg[row, 0] = 1.0, -1.0              # w_j - w_1
        for node in (0, j):                                     # trapezoid rule on the rotation along the edge
            Bg[row, 3 * node + 2] += 0.5 * dx                   # beta_x = thy
            Bg[row, 3 * node + 1] += -0.5 * dy                  # beta_y = -thx
    G = np.array([[x[1] - x[0], y[1] - y[0]], [x[2] - x[0], y[2] - y[0]]])
    Bs = np.linalg.solve(G, Bg)
    Ks = kappa * (E / (2 * (1 + nu))) * t * A * Bs.T @ Bs

    Kl = np.zeros((18, 18))
    mem = [0, 1, 6, 7, 12, 13]
    bend = [2, 3, 4, 8, 9, 10, 14, 15, 16]
    Kl[np.ix_(mem, mem)] += Km
    Kl[np.ix_(bend, bend)] += Kb + Ks
    # (Optional element-level drilling term, off by default: the model applies a per-node version, see FEModel._drilling, which
    # also removes the spurious zero-energy patterns an element-level constraint leaves on structured meshes.)
    r = np.zeros(18)
    for i in range(3):
        r[6 * i] += 0.5 * Ny[i]                                 # -(1/2)(-du/dy)
        r[6 * i + 1] += -0.5 * Nx[i]                            # -(1/2)(dv/dx)
        r[6 * i + 5] += 1.0 / 3.0
    Kl += (drill * E / (2 * (1 + nu)) * t * A) * np.outer(r, r)

    T = np.zeros((18, 18))
    for i in range(6):
        T[3 * i:3 * i + 3, 3 * i:3 * i + 3] = R
    Kg = T.T @ Kl @ T

    me = rho * t * A
    m_node = me / 3
    i_rot = rho * t ** 3 * A / 36 + 1e-6 * m_node * A          # tiny rotary inertia keeps M positive definite
    Mdiag = np.tile([m_node, m_node, m_node, i_rot, i_rot, i_rot], 3)
    return Kg, Mdiag, R, A


# --------------------------------------------------------------------------- #
# Mesh
# --------------------------------------------------------------------------- #
class Mesh:
    """Merged node list + triangle list + per-triangle properties."""

    def __init__(self):
        self.nodes: list = []
        self._index: dict = {}
        self.tris: list = []                    # (n1, n2, n3)
        self.tri_plate: list = []               # plate index per triangle
        self.plates: list = []
        self.plate_nodes: list = []             # per plate: list of node ids
        self.plate_grid: list = []              # per plate: 2D array of node ids (ny+1, nx+1)

    def node(self, p: np.ndarray, part_scope: str) -> int:
        key = (part_scope, round(float(p[0]) / 1e-4), round(float(p[1]) / 1e-4), round(float(p[2]) / 1e-4))
        if key in self._index:
            return self._index[key]
        self._index[key] = len(self.nodes)
        self.nodes.append(np.array(p, dtype=float))
        return self._index[key]

    def add_plate(self, plate: Plate, nx: int, ny: int) -> None:
        pid = len(self.plates)
        plate.nx, plate.ny = nx, ny
        self.plates.append(plate)
        grid = np.zeros((ny + 1, nx + 1), dtype=int)
        for j in range(ny + 1):
            for i in range(nx + 1):
                p = plate.centre + plate.e1 * ((i / nx - 0.5) * plate.a) + plate.e2 * ((j / ny - 0.5) * plate.b)
                grid[j, i] = self.node(p, plate.part)
        self.plate_grid.append(grid)
        self.plate_nodes.append(sorted(set(grid.ravel().tolist())))
        for j in range(ny):
            for i in range(nx):
                n00, n10, n01, n11 = grid[j, i], grid[j, i + 1], grid[j + 1, i], grid[j + 1, i + 1]
                # alternate the diagonal for a symmetric mesh
                if (i + j) % 2 == 0:
                    quad = [(n00, n10, n11), (n00, n11, n01)]
                else:
                    quad = [(n00, n10, n01), (n10, n11, n01)]
                for tri in quad:
                    self.tris.append(tri)
                    self.tri_plate.append(pid)

    @property
    def xyz(self) -> np.ndarray:
        return np.array(self.nodes)


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
class FEModel:
    """
    Build with plates and lumped masses (or FEModel.from_design), then call modal() / static().

    Ties: pass `ties=True` (default) to join nodes of different parts that sit on another part's mid-surface (an attached edge).
    """

    def __init__(self, plates: list[Plate], lumps: list[LumpedMass] | None = None, mesh_size: float | None = None,
                 target_elements: int = 6000, tie: bool = True):
        self.plates = plates
        self.lumps = lumps or []
        self.warnings: list[str] = []
        self.mesh = Mesh()
        total_area = sum(p.area for p in plates) or 1.0
        h = mesh_size or math.sqrt(2 * total_area / max(target_elements, 100))
        self.mesh_size = h
        for p in plates:
            nx = p.nx or max(2, math.ceil(p.a / h))
            ny = p.ny or max(2, math.ceil(p.b / h))
            self.mesh.add_plate(p, nx, ny)
        self.ndof = DOF * len(self.mesh.nodes)
        self.fixed: set = set()                 # constrained dof indices
        self._slave: dict = {}                  # dof -> list[(master dof, weight)]
        self.ties: list = []
        self._extra_mass = np.zeros(self.ndof)  # translational lumped mass added per dof
        self._tie_enabled = tie
        self._assembled = False

    # ---- constraints ------------------------------------------------------------------------
    def fix_nodes(self, nodes, dofs=(0, 1, 2, 3, 4, 5)) -> None:
        for n in nodes:
            for d in dofs:
                self.fixed.add(DOF * int(n) + d)

    def nodes_where(self, predicate, plate_names=None) -> list[int]:
        xyz = self.mesh.xyz
        out = set()
        for pid, pl in enumerate(self.mesh.plates):
            if plate_names is not None and pl.name not in plate_names and pl.part not in plate_names:
                continue
            for n in self.mesh.plate_nodes[pid]:
                if predicate(xyz[n]):
                    out.add(n)
        return sorted(out)

    def boundary_nodes(self, plate_index: int) -> list[int]:
        g = self.mesh.plate_grid[plate_index]
        edge = set(g[0, :]) | set(g[-1, :]) | set(g[:, 0]) | set(g[:, -1])
        return sorted(int(n) for n in edge)

    # ---- assembly -------------------------------------------------------------------------------
    def _ties_between(self, pa: int, pb: int, tri_by_plate: dict) -> list:
        """Nodes of plate A that lie on plate B's mid-surface (an attached edge), with their interpolation onto B."""
        mesh, xyz = self.mesh, self.mesh.xyz
        A, B = mesh.plates[pa], mesh.plates[pb]
        n3 = B.e3
        parallel = abs(float(np.dot(A.e3, n3))) > 0.999            # face-to-face contact vs an attached edge
        limit = (A.t + B.t) / 2 if parallel else B.t / 2
        found = []
        for n in mesh.plate_nodes[pa]:
            p = xyz[n]
            d = float(np.dot(p - B.centre, n3))
            if abs(d) > limit * 1.05 + 1e-3:
                continue
            q = p - d * n3                                  # projection onto B's mid-surface
            u, v = np.dot(q - B.centre, B.e1), np.dot(q - B.centre, B.e2)
            if abs(u) > B.a / 2 + 1e-3 or abs(v) > B.b / 2 + 1e-3:
                continue
            hit = self._locate(B, pb, q, tri_by_plate)
            if hit is not None:
                found.append((n, hit[0], hit[1], p - q))
        return found

    def _build_ties(self) -> None:
        """Join nodes of one part that lie on another part's mid-surface (the attached edge) to that plate (exact elimination)."""
        mesh = self.mesh
        tri_by_plate: dict = {}
        for ti, pid in enumerate(mesh.tri_plate):
            tri_by_plate.setdefault(pid, []).append(ti)
        slave_nodes: set = set()
        for pa in range(len(mesh.plates)):
            for pb in range(pa + 1, len(mesh.plates)):
                A, B = mesh.plates[pa], mesh.plates[pb]
                if A.part == B.part:
                    continue
                ab, ba = self._ties_between(pa, pb, tri_by_plate), self._ties_between(pb, pa, tri_by_plate)
                pinned = lambda ties: any(DOF * t[0] + k in self.fixed for t in ties for k in range(3))
                if ab and ba:                                # both directions touch (coplanar neighbours): smaller plate is the slave
                    chosen = ab if (A.area, pa) <= (B.area, pb) else ba
                    other = ba if chosen is ab else ab
                    if pinned(chosen) and not pinned(other):  # a supported node cannot be a slave: it would cut the tie
                        chosen = other
                else:
                    chosen = ab or ba
                for tie in chosen:
                    if tie[0] in slave_nodes:
                        continue
                    slave_nodes.add(tie[0])
                    self.ties.append(tie)
        for n, tri_nodes, weights, r in self.ties:
            for a in range(3):                              # translation: u_s = sum N_i (u_i + theta_i x r)
                row = DOF * n + a
                comb: dict = {}
                for ni, w in zip(tri_nodes, weights):
                    comb[DOF * ni + a] = comb.get(DOF * ni + a, 0.0) + w
                    b_, c_ = (a + 1) % 3, (a + 2) % 3      # (theta x r)_a = theta_b r_c - theta_c r_b
                    comb[DOF * ni + 3 + b_] = comb.get(DOF * ni + 3 + b_, 0.0) + w * r[c_]
                    comb[DOF * ni + 3 + c_] = comb.get(DOF * ni + 3 + c_, 0.0) - w * r[b_]
                self._slave[row] = list(comb.items())
            for a in range(3):                              # rotations follow the master
                self._slave[DOF * n + 3 + a] = [(DOF * ni + 3 + a, w) for ni, w in zip(tri_nodes, weights)]

    def _locate(self, B: Plate, pb: int, q: np.ndarray, tri_by_plate: dict):
        """Triangle of plate B containing the point q (barycentric weights), or None."""
        xyz, mesh = self.mesh.xyz, self.mesh
        best = None
        for ti in tri_by_plate[pb]:
            n1, n2, n3 = mesh.tris[ti]
            P = xyz[[n1, n2, n3]]
            T = np.array([P[0] - P[2], P[1] - P[2]]).T
            try:
                lam12 = np.linalg.lstsq(T, q - P[2], rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            lam = np.array([lam12[0], lam12[1], 1 - lam12.sum()])
            slack = -lam.min()
            if slack < 1e-6:
                return (n1, n2, n3), lam
            if best is None or slack < best[0]:
                best = (slack, (n1, n2, n3), lam)
        if best is not None and best[0] < 1e-3:
            lam = np.clip(best[2], 0, None)
            return best[1], lam / lam.sum()
        return None

    def _drilling(self) -> sp.csr_matrix:
        """
        Drilling stiffness, applied per node and per plate: penalise the difference between the node's rotation about the plate
        normal and the area-weighted average in-plane rotation (1/2)(dv/dx - du/dy) of the plate's elements around it. This is exactly
        zero for any rigid-body motion, but it fixes every drilling rotation, so no spurious zero-energy patterns survive.
        """
        mesh, xyz, nd = self.mesh, self.mesh.xyz, self.ndof
        rows, cols, vals, weights = [], [], [], []
        row_id = 0
        for pid, pl in enumerate(mesh.plates):
            per_node: dict = {}                                  # node -> list of (element area / 3, {dof: coefficient})
            for ti, (n1, n2, n3) in enumerate(mesh.tris):
                if mesh.tri_plate[ti] != pid:
                    continue
                X = xyz[[n1, n2, n3]]
                e1 = (X[1] - X[0]) / np.linalg.norm(X[1] - X[0])
                nrm = np.cross(X[1] - X[0], X[2] - X[0])
                A = 0.5 * np.linalg.norm(nrm)
                e2 = np.cross(nrm / (2 * A), e1)
                x = np.array([0.0, (X[1] - X[0]) @ e1, (X[2] - X[0]) @ e1])
                y = np.array([0.0, (X[1] - X[0]) @ e2, (X[2] - X[0]) @ e2])
                Nx = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]]) / (2 * A)
                Ny = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]]) / (2 * A)
                coeff: dict = {}
                for k, node in enumerate((n1, n2, n3)):          # omega = 1/2 sum_k ( Nx_k (e2 . u_k) - Ny_k (e1 . u_k) )
                    vec = 0.5 * (Nx[k] * e2 - Ny[k] * e1)
                    for comp in range(3):
                        coeff[DOF * node + comp] = coeff.get(DOF * node + comp, 0.0) + vec[comp]
                for node in (n1, n2, n3):
                    per_node.setdefault(node, []).append((A / 3, coeff))
            gamma = 0.02 * pl.E / (2 * (1 + pl.nu)) * pl.t
            n_vec = pl.e3
            for node, items in per_node.items():
                V = sum(a for a, _ in items)
                for comp in range(3):                            # n . theta
                    rows.append(row_id); cols.append(DOF * node + 3 + comp); vals.append(n_vec[comp])
                for a, coeff in items:                           # - average in-plane rotation
                    for dof, c in coeff.items():
                        rows.append(row_id); cols.append(dof); vals.append(-(a / V) * c)
                weights.append(gamma * V)
                row_id += 1
        R = sp.csr_matrix((vals, (rows, cols)), shape=(row_id, nd))
        return (R.T @ sp.diags(weights) @ R).tocsr()

    def _assemble(self) -> None:
        mesh, xyz = self.mesh, self.mesh.xyz
        nd = self.ndof
        rows, cols, vals = [], [], []
        M = np.zeros(nd)
        self.elem = []
        for ti, (n1, n2, n3) in enumerate(mesh.tris):
            pl = mesh.plates[mesh.tri_plate[ti]]
            Kg, Md, R, A = _shell_element(xyz[[n1, n2, n3]], pl.E, pl.nu, pl.t, pl.rho)
            idx = np.concatenate([np.arange(DOF * n, DOF * n + DOF) for n in (n1, n2, n3)])
            ii, jj = np.meshgrid(idx, idx, indexing="ij")
            rows.append(ii.ravel()); cols.append(jj.ravel()); vals.append(Kg.ravel())
            M[idx] += Md
            self.elem.append((idx, R, A))
        self.K = sp.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(nd, nd)) + self._drilling()
        self.M = M + self._extra_mass
        self._assembled = True

    def _add_lumps(self) -> None:
        mesh, xyz = self.mesh, self.mesh.xyz
        for lump in self.lumps:
            best = []
            for pid, pl in enumerate(mesh.plates):
                if lump.attach_to and pl.part not in lump.attach_to:
                    continue
                d = abs(float(np.dot(lump.at - pl.centre, pl.e3)))
                best.append((d, pid))
            if not best:
                self.warnings.append(f"Lumped mass '{lump.name}' has no structure to attach to; its mass is ignored.")
                continue
            _, pid = min(best)
            nodes = np.array(mesh.plate_nodes[pid])
            dist = np.linalg.norm(xyz[nodes] - lump.at, axis=1)
            k = min(12, len(nodes))
            pick = np.argsort(dist)[:k]
            w = 1.0 / (dist[pick] + 1.0)
            w /= w.sum()
            for n, wi in zip(nodes[pick], w):
                for a in range(3):
                    self._extra_mass[DOF * int(n) + a] += lump.mass * wi

    def _reduction(self):
        """Matrix P mapping the independent (free, non-slave) dofs to all dofs."""
        nd = self.ndof
        independent = [d for d in range(nd) if d not in self._slave and d not in self.fixed]
        col_of = {d: i for i, d in enumerate(independent)}
        r, c, v = [], [], []
        for d in independent:
            r.append(d); c.append(col_of[d]); v.append(1.0)
        for d, comb in self._slave.items():
            if d in self.fixed:
                continue
            for m, w in comb:
                if m in col_of:
                    r.append(d); c.append(col_of[m]); v.append(w)
                elif m in self._slave:                      # chained slave (rare): expand one level
                    for m2, w2 in self._slave[m]:
                        if m2 in col_of:
                            r.append(d); c.append(col_of[m2]); v.append(w * w2)
        P = sp.csr_matrix((v, (r, c)), shape=(nd, len(independent)))
        return P, independent

    def components(self) -> list[list[str]]:
        """Groups of plates that are connected to each other (by shared nodes or ties). More than one means loose pieces."""
        self.prepare()
        mesh = self.mesh
        parent = list(range(len(mesh.plates)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        owner: dict = {}
        for pid, nodes in enumerate(mesh.plate_nodes):
            for n in nodes:
                if n in owner:                                  # a node shared by two plates (a merged edge) joins them
                    parent[find(pid)] = find(owner[n])
                owner.setdefault(n, pid)
        for n, tri_nodes, _, _ in self.ties:
            parent[find(owner[n])] = find(owner[tri_nodes[0]])
        groups: dict = {}
        for pid, pl in enumerate(mesh.plates):
            groups.setdefault(find(pid), []).append(pl.name)
        return list(groups.values())

    def prepare(self) -> None:
        if self._assembled:
            return
        if self._tie_enabled:
            self._build_ties()
        self._add_lumps()
        self._assemble()
        groups = self.components()
        if len(groups) > 1:
            self.warnings.append(f"The structure is {len(groups)} separate pieces that are not connected to each other: "
                                 + "; ".join("[" + ", ".join(g[:4]) + (" ..." if len(g) > 4 else "") + "]" for g in groups)
                                 + ". Parts must touch (share an edge or an attached face) to be joined.")

    # ---- analyses --------------------------------------------------------------------------------
    def total_mass(self) -> float:
        self.prepare()
        mt = self.M.reshape(-1, DOF)[:, :3]
        return float(mt.sum() / 3.0)

    def modal(self, n_modes: int = 10) -> dict:
        """Natural frequencies (Hz), effective modal mass fractions in X/Y/Z, and mode shapes."""
        self.prepare()
        P, free = self._reduction()
        K = (P.T @ self.K @ P).tocsc()
        M = sp.diags(self.M).tocsr()
        Mr = (P.T @ M @ P).tocsc()
        n = K.shape[0]
        pieces = len(self.components())
        loose = 0 if self.fixed else 6 * pieces                 # rigid-body modes of a free structure: 6 per separate piece
        want = min(n_modes + loose + 2, n - 2)
        sigma = -1.0e3                                      # below zero so free-free (singular) K is fine
        try:
            w2, vec = spla.eigsh(K, k=want, M=Mr, sigma=sigma, which="LM", ncv=min(n, max(2 * want + 1, 30)), maxiter=400)
        except spla.ArpackNoConvergence:
            raise ValueError("The modal solver did not converge. This usually means loose parts: the structure has "
                             f"{pieces} separate piece(s). Check the modelling notes and make the parts touch.") from None
        order = np.argsort(w2)
        w2, vec = w2[order], vec[:, order]
        # drop rigid-body modes (zero frequency) of a free structure
        keep = w2 > 1e-3                                    # rigid-body modes are ~1e-9 or smaller; 1e-3 is 0.005 Hz
        rigid = int((~keep).sum())
        w2, vec = w2[keep][:n_modes], vec[:, keep][:, :n_modes]
        freqs = np.sqrt(np.clip(w2, 0, None)) / (2 * math.pi)
        # effective modal mass: influence vectors for unit translation in x, y, z
        mt = float(self.M.reshape(-1, DOF)[:, :3].sum() / 3.0)
        eff = []
        for a in range(3):
            r = np.zeros(self.ndof)
            r[a::DOF] = 1.0
            rr = P.T @ (self.M * r)                          # M r projected onto the free dofs
            part = []
            for k in range(vec.shape[1]):
                phi = vec[:, k]
                gamma = float(phi @ rr)
                mk = float(phi @ (Mr @ phi))
                part.append(gamma ** 2 / mk / mt)
            eff.append(part)
        shapes = []
        for k in range(min(vec.shape[1], 6)):
            full = P @ vec[:, k]
            u = full.reshape(-1, DOF)[:, :3]
            shapes.append(u / (np.abs(u).max() or 1.0))
        return dict(frequencies_hz=freqs.tolist(), effective_mass_fraction=dict(x=eff[0], y=eff[1], z=eff[2]),
                    cumulative_effective_mass=dict(x=float(sum(eff[0])), y=float(sum(eff[1])), z=float(sum(eff[2]))),
                    rigid_body_modes_removed=rigid, mode_shapes=shapes, total_mass_tonne=mt,
                    dofs=int(n), nodes=len(self.mesh.nodes), elements=len(self.mesh.tris))

    def static(self, accel_g=(0.0, 0.0, 0.0), pressure: dict | None = None, point_loads: dict | None = None,
               safety_factor: float = 1.25) -> dict:
        """
        Static solve. accel_g: acceleration in g applied to the whole structure (inertia loads on all mass).
        pressure: {plate name or part: pressure in MPa along that plate's normal e3}. point_loads: {node: (fx, fy, fz)} in N.
        Fixed dofs are the supports; a structure with none needs inertia relief, which is not provided (an error explains).
        """
        self.prepare()
        if not self.fixed:
            raise ValueError("A static analysis needs at least one support (fixed nodes). Use modal analysis for a free structure.")
        nd = self.ndof
        supported = {d // DOF for d in self.fixed}
        for group in self.components():
            plate_ids = [i for i, pl in enumerate(self.mesh.plates) if pl.name in group]
            if not any(n in supported for i in plate_ids for n in self.mesh.plate_nodes[i]):
                raise ValueError(f"These parts are not connected to any support, so a static analysis can't be solved: "
                                 f"{group[:6]}. Make them touch the supported structure.")
        f = np.zeros(nd)
        a = np.array(accel_g, dtype=float) * G0                  # mm/s^2
        for d in range(3):
            f[d::DOF] -= self.M[d::DOF] * a[d]                   # inertia load = -m a  (a is the acceleration of the support)
        # note: body force convention: structure accelerates with the support, so each mass sees F = -m*a in the frame
        # of the support; reactions are then equal and opposite.
        mesh, xyz = self.mesh, self.mesh.xyz
        if pressure:
            for ti, (n1, n2, n3) in enumerate(mesh.tris):
                pl = mesh.plates[mesh.tri_plate[ti]]
                q = pressure.get(pl.name, pressure.get(pl.part))
                if q is None:
                    continue
                _, R, A = self.elem[ti]
                load = q * A / 3 * pl.e3
                for n in (n1, n2, n3):
                    f[DOF * n:DOF * n + 3] += load
        for n, vec in (point_loads or {}).items():
            f[DOF * int(n):DOF * int(n) + 3] += np.array(vec, dtype=float)
        P, free = self._reduction()
        K = (P.T @ self.K @ P).tocsc()
        u_r = spla.spsolve(K, P.T @ f)
        u = P @ u_r
        # Reaction at the supports: every dof that is rigidly tied to a fixed dof (a slave whose master is fixed) passes its
        # load into that support too, so those rows are summed onto the fixed dofs through the tie weights.
        fixed_idx = np.array(sorted(self.fixed))
        col_of = {d: i for i, d in enumerate(fixed_idx)}
        r, c, v = [int(d) for d in fixed_idx], list(range(len(fixed_idx))), [1.0] * len(fixed_idx)
        for d, comb in self._slave.items():
            if d in self.fixed:
                continue
            for m, w in comb:
                if m in col_of:
                    r.append(d); c.append(col_of[m]); v.append(w)
                elif m in self._slave:
                    for m2, w2 in self._slave[m]:
                        if m2 in col_of:
                            r.append(d); c.append(col_of[m2]); v.append(w * w2)
        T = sp.csr_matrix((v, (r, c)), shape=(nd, len(fixed_idx)))
        reaction = T.T @ (self.K @ u - f)
        rx = [float(reaction[fixed_idx % DOF == d].sum()) for d in range(3)]
        un = u.reshape(-1, DOF)[:, :3]
        disp = np.linalg.norm(un, axis=1)
        # stress recovery per triangle (top/bottom von Mises)
        vm_max, worst = np.zeros(len(mesh.tris)), np.zeros(len(mesh.tris), dtype=int)
        for ti, (n1, n2, n3) in enumerate(mesh.tris):
            pl = mesh.plates[mesh.tri_plate[ti]]
            idx, R, A = self.elem[ti]
            ul = np.array([R @ u[DOF * n:DOF * n + 3] for n in (n1, n2, n3)])
            tl = np.array([R @ u[DOF * n + 3:DOF * n + 6] for n in (n1, n2, n3)])
            vm_max[ti] = self._von_mises(xyz[[n1, n2, n3]], R, ul, tl, pl)
        centroids = xyz[np.array(mesh.tris)].mean(axis=1)
        stress = {}
        for pid, pl in enumerate(mesh.plates):
            sel = [ti for ti, p_ in enumerate(mesh.tri_plate) if p_ == pid]
            s = float(vm_max[sel].max()) if sel else 0.0
            ms = None if not pl.yield_mpa or s <= 0 else pl.yield_mpa / (safety_factor * s) - 1.0
            stress[pl.name] = dict(max_von_mises_mpa=s, yield_mpa=pl.yield_mpa, margin_of_safety=ms)
        worst_plate = max(stress, key=lambda k: stress[k]["max_von_mises_mpa"])
        return dict(max_displacement_mm=float(disp.max()), max_von_mises_mpa=float(vm_max.max()),
                    reaction_force_n=rx, stress_by_plate=stress, worst_plate=worst_plate, displacements_mm=un,
                    von_mises_by_element_mpa=vm_max, element_centroids_mm=centroids, safety_factor=safety_factor,
                    nodes=len(mesh.nodes), elements=len(mesh.tris), total_mass_tonne=self.total_mass())

    @staticmethod
    def _von_mises(X, R, ul, tl, pl: Plate) -> float:
        """Maximum (top/bottom surface) von Mises stress of one triangle from its local displacements and rotations."""
        x1, x2, x3 = X
        d12, d13 = x2 - x1, x3 - x1
        e1 = R[0]
        e2 = R[1]
        x = np.array([0.0, d12 @ e1, d13 @ e1])
        y = np.array([0.0, d12 @ e2, d13 @ e2])
        A = 0.5 * abs((x[1] - x[0]) * (y[2] - y[0]) - (x[2] - x[0]) * (y[1] - y[0]))
        b = np.array([y[1] - y[2], y[2] - y[0], y[0] - y[1]])
        c = np.array([x[2] - x[1], x[0] - x[2], x[1] - x[0]])
        Nx, Ny = b / (2 * A), c / (2 * A)
        E, nu, t = pl.E, pl.nu, pl.t
        c0 = E / (1 - nu ** 2)
        D = c0 * np.array([[1, nu, 0], [nu, 1, 0], [0, 0, (1 - nu) / 2]])
        eps_m = np.array([Nx @ ul[:, 0], Ny @ ul[:, 1], Ny @ ul[:, 0] + Nx @ ul[:, 1]])
        kap = np.array([Nx @ tl[:, 1], -(Ny @ tl[:, 0]), Ny @ tl[:, 1] - Nx @ tl[:, 0]])
        s_m = D @ eps_m
        s_b = D @ kap * (t / 2)
        worst = 0.0
        for sign in (+1, -1):
            sx, sy, txy = s_m + sign * s_b
            worst = max(worst, math.sqrt(max(sx * sx - sx * sy + sy * sy + 3 * txy * txy, 0.0)))
        return worst


# --------------------------------------------------------------------------- #
# Building a model from a Design
# --------------------------------------------------------------------------- #
def _frame_of_slab(part):
    """Principal frame of a thin box-like part: (centre, e1, e2, a, b, t) using its largest flat face, or None if not thin."""
    solid = part.solid
    faces = [f for f in solid.faces()]
    best = None
    for f in faces:
        try:
            n = f.normal_at()
        except Exception:
            continue
        if best is None or f.area > best[0]:
            best = (f.area, f)
    if best is None:
        return None
    face = best[1]
    n = np.array([face.normal_at().X, face.normal_at().Y, face.normal_at().Z])
    n = n / np.linalg.norm(n)
    edges = face.edges()
    e1 = None
    longest = 0.0
    for e in edges:
        p0, p1 = e.start_point(), e.end_point()
        d = np.array([p1.X - p0.X, p1.Y - p0.Y, p1.Z - p0.Z])
        ln = np.linalg.norm(d)
        if ln > longest:
            longest, e1 = ln, d / ln
    if e1 is None:
        return None
    e1 = e1 - np.dot(e1, n) * n
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n, e1)
    verts, _ = solid.tessellate(1.0, 0.5)
    pts = np.array([[v.X, v.Y, v.Z] for v in verts])
    lo = np.array([pts @ e1, pts @ e2, pts @ n]).min(axis=1)
    hi = np.array([pts @ e1, pts @ e2, pts @ n]).max(axis=1)
    a, b, t = hi - lo
    centre = ((lo + hi) / 2)[0] * e1 + ((lo + hi) / 2)[1] * e2 + ((lo + hi) / 2)[2] * n
    return centre, e1, e2, float(a), float(b), float(t)


def from_design(design, mesh_size: float | None = None, target_elements: int = 6000, thin_ratio: float = 0.1,
                fixed: dict | None = None, constraint: str = "perimeter") -> FEModel:
    """
    Build an FE model of a Design.
      * thin box-like parts (panels, plates)              -> one shell plate each
      * hollow chassis (wall > 0, axis-aligned)           -> six shell plates for its walls
      * everything else (equipment, tanks, screws)        -> lumped masses attached to nearby structure
    fixed: {"part": name, "side": "-z"} clamps that part's face (perimeter nodes by default, or the whole face with
    constraint="face"). Without `fixed` the structure is free (modal analysis then reports free-free modes).
    """
    plates: list[Plate] = []
    lumps: list[LumpedMass] = []
    notes: list[str] = []
    for part in design.parts.values():
        mat = get_material(part.material)
        rho = mat.density * 1e-12
        usable = mat.structural and mat.youngs_gpa and mat.poisson
        p = part.params
        bb_min, bb_max = part.bbox()
        centre_bb = np.array([(bb_min[i] + bb_max[i]) / 2 for i in range(3)])
        if part.kind == "chassis" and p.get("wall", 0) > 0 and usable and not any(abs(p.get(k, 0)) > 1e-9 for k in ("rx", "ry", "rz")) \
                and not (p.get("assembly") and design._asm_artic.get(p["assembly"], False)):
            L, W, H, w = p["length"], p["width"], p["height"], p["wall"]
            for axis, name in ((0, "x"), (1, "y"), (2, "z")):
                dims = [L - w, W - w, H - w]
                for sign, tag in ((1, "+"), (-1, "-")):
                    c = centre_bb.copy()
                    c[axis] += sign * dims[axis] / 2
                    others = [i for i in range(3) if i != axis]
                    e1 = np.zeros(3); e1[others[0]] = 1.0
                    e2 = np.zeros(3); e2[others[1]] = 1.0
                    plates.append(Plate(f"{part.name}{tag}{name}", c, e1, e2, dims[others[0]], dims[others[1]], w,
                                        mat.youngs_gpa * 1000, mat.poisson, rho, mat.yield_mpa, part.name))
            if p.get("internal_mass_kg"):
                lumps.append(LumpedMass(f"{part.name}_equipment", p["internal_mass_kg"] * 1e-3, centre_bb, [part.name]))
            if p.get("fillet") or p.get("chamfer"):
                notes.append(f"'{part.name}': rounded/bevelled edges are ignored (sharp box walls).")
            continue
        frame = _frame_of_slab(part) if usable and part.kind not in ("sphere", "cylinder", "fastener") else None
        if frame is not None:
            c, e1, e2, a, b, t = frame
            if t <= thin_ratio * min(a, b):
                if mat.caveat and "sandwich" in mat.caveat.lower():
                    notes.append(f"'{part.name}': {mat.caveat}")
                fill = part.solid.volume / (a * b * t)
                rho_eff = rho
                if fill < 0.97:                                # much of the rectangle is empty (a ring, a frame): keep the MASS right
                    rho_eff = rho * fill
                    notes.append(f"'{part.name}': not a full rectangle ({100 * fill:.0f}% filled); modelled as a rectangular plate "
                                 "with its density reduced to keep the mass correct (stiffness is overestimated).")
                elif fill < 0.9999:
                    notes.append(f"'{part.name}': cut-outs / holes are ignored; modelled as a full rectangular plate.")
                plates.append(Plate(part.name, c, e1, e2, a, b, t, mat.youngs_gpa * 1000, mat.poisson, rho_eff, mat.yield_mpa,
                                    part.name))
                continue
        lumps.append(LumpedMass(part.name, part.mass_kg * 1e-3, centre_bb))
    if not plates:
        raise ValueError("Nothing in this design is a thin-walled plate or shell, so the shell solver has no structure to model. "
                         "Use a hollow chassis (wall > 0) or thin panels.")
    model = FEModel(plates, lumps, mesh_size=mesh_size, target_elements=target_elements)
    model.warnings += notes
    if fixed:
        part, side = fixed["part"], fixed["side"]
        axis, sign = "xyz".index(side[1]), (1 if side[0] == "+" else -1)
        cands = [i for i, pl in enumerate(model.mesh.plates) if pl.part == part and abs(pl.e3[axis]) > 0.99]
        if not cands:
            raise ValueError(f"'{part}' has no plate facing {side} to fix. Plates: "
                             f"{sorted({pl.name for pl in model.mesh.plates if pl.part == part}) or 'none (part is not a plate)'}.")
        pid = max(cands, key=lambda i: sign * model.mesh.plates[i].centre[axis])
        nodes = model.boundary_nodes(pid) if constraint == "perimeter" else model.mesh.plate_nodes[pid]
        model.fix_nodes(nodes)
    return model
