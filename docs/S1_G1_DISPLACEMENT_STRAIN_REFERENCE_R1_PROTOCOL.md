# S1-G1 小位移/应变参考集 R1

## 目的与边界

本协议生成一组小而完整的有限温 KSDFT 参考数据，用于验证后续密度基组流程能否处理非平衡原子位移与均匀应变。它只闭合 G1 的“位移/应变参考集”数据准备项；中心差分响应仅作诊断，不构成 G4 力/应力精度验收。

所有标签保持 Mermin 有限温语义。`E_ec=F-m/2` 是熵修正估计量，禁止表述为严格零温能量。

## 冻结父证据与数值参数

- 基准提交：`f10008dd7a6e05cb8bf23f1ffd10e99e19b6083d`。
- Al 父运行：`S1-20260807-043`，tree `f6aebad35880164176afe032f91e0de65d2aa456`。
- Mg 父运行：`S1-20260807-045`，tree `6ffbe064f97f60e3f12782f49824740a854c7422`。
- `ecutwfc/ecutrho=40/160 Ry`，`scf_thr=1e-10`，Fermi-Dirac `sigma=0.001837465 Ry`。
- Al 使用 PBE、`28×28×28`；Mg 延续父证据的 LDA-PZ、`24×24×16`。
- `cal_force=1`、`cal_stress=1`、`out_chg 1 17`、`out_pot 1 17`。

## 15 点矩阵

| ID | 体系与扰动 |
|---|---|
| 201 | Al 原胞沿第一晶格矢量扩成 `2×1×1`，无位移 |
| 202/203 | Al 超胞第二原子 Cartesian x 位移 `±0.010 Å` |
| 204/205 | Al 等体积 tetragonal 应变 `s=±0.005` |
| 206/207 | Al xy simple shear `s=±0.005` |
| 208/209 | Mg 第二原子 basal Cartesian x 位移 `±0.010 Å` |
| 210/211 | Mg 第二原子 c Cartesian z 位移 `±0.010 Å` |
| 212/213 | Mg 等体积 axial 应变 `s=±0.005` |
| 214/215 | Mg xz simple shear `s=±0.005` |

完整实验号为 `S1-20260810-201` 至 `S1-20260810-215`。

## 应变矩阵约定

采用 Cartesian 列向量约定 `r'=F r`。STRU 中晶格矢量按行保存，因此 `A'=A F^T`；应变点的 Direct 原子坐标不变。

- Al tetragonal：`F=diag(1+s,(1+s)^(-1/2),(1+s)^(-1/2))`，x 为伸缩轴，`det(F)=1`。
- Mg axial：`F=diag((1+s)^(-1/2),(1+s)^(-1/2),1+s)`，z/c 为伸缩轴，`det(F)=1`。
- Al xy shear：`F=I+s e_x⊗e_y`。
- Mg xz shear：`F=I+s e_x⊗e_z`。

位移点先在 Cartesian 空间施加位移，再用实际超胞/晶胞矩阵的逆矩阵机械转换为 Direct 坐标。config 和每点 metadata 同时冻结 3×3 矩阵、Cartesian 位移、fractional 位移及预期 Bohr 晶格/坐标。

## 单次执行与计算资源

- 外部 state 固定为 `/home/shenwei01/.local/state/m_ofdft/g1_displacement_strain_reference_r1_20260810`。
- 15 个 ID 均为单次使用。runner 在求解器启动前以 `O_CREAT|O_EXCL` 和目录 fsync 写 attempt marker；任一 ID 已有 marker、run 或 completion 即拒绝运行，失败不重试。
- 正式求解固定在 `node01`，4 个 MPI rank 分别由 rank wrapper 精确绑定至逻辑 CPU `40,41,42,43`，每点保存绑定前后证据。
- 不读取、修改或复用 R1–R4/DFTpy 的任何外部 state。

正式启动：

```bash
/usr/bin/python3 -s scripts/run_s1_g1_displacement_strain_reference.py --all
```

## 硬验收

1. 注册/收敛/accepted 运行均为 `15/15`，失败、缺失和重试 ID 均为空。
2. signed pair 为 `7/7`；中心差分只写 diagnostic，不设置 G4 结论。
3. 每点 12 个有限热力学标签完整；`m<=0`、`F<=E_ec<=U`，全部恒等式残差严格小于 `1e-8 eV/atom`。
4. 每点力为 `Natom×3`、应力为 `3×3`，所有数值有限。
5. 密度/势 cube 均来自 17 位输出控制，数值有限；cube 晶格矢量和原子坐标相对冻结 STRU 的最大绝对误差严格小于 `5e-5 bohr`。
6. 独立积分 `Ngrid=ΔV Σrho`，相对电子数误差严格小于 `1e-10`。
7. 正式 hostname 为 `node01`，四个 rank 的实际 affinity 分别严格为 `40–43`。

分析、预提交验证与提交后验证：

```bash
/usr/bin/python3 -s scripts/analyze_s1_g1_displacement_strain_reference.py --write
/usr/bin/python3 -s scripts/validate_s1_g1_displacement_strain_reference.py
/usr/bin/python3 -s scripts/validate_s1_g1_displacement_strain_reference.py --require-committed
```
