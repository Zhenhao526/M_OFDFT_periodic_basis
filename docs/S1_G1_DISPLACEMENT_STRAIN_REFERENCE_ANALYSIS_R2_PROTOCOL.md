# S1-G1 小位移/应变参考集：analysis-only R2

## 修订原因

R1 求解器执行本身完整结束，但 R1 analyzer 对 cube 原子坐标采用裸 Cartesian 相减。`S1-20260810-203` 的负位移原子被 ABACUS 合法地映射到周期相邻晶胞，导致约一个晶格平移被误判为几何错误。该结论是分析器 false negative，不是求解失败，也不许可重跑或重用 ID。

R2 只修订分析。它复用外部只读 state `/home/shenwei01/.local/state/m_ofdft/g1_displacement_strain_reference_r1_20260810` 中 201–215 的 15 个唯一正式运行，不启动求解器、不产生新 ID。

## PBC 修正

对每个 cube 原子行和冻结 STRU 期望坐标：

1. 计算 `delta_cart = r_cube-r_STRU`；
2. 用 row-lattice 的逆矩阵计算 `delta_frac = delta_cart A^-1`；
3. 分量减去最近整数 `delta_frac -= round(delta_frac)`；
4. 变回 `delta_min = delta_frac A`，以其最大绝对分量执行 `5e-5 bohr` 门。

晶格门不做 minimum-image：仍严格比较 `n_i*axis_step_i` 与 STRU Cartesian 晶格矢量。density/potential 必须具有相同 origin、网格、axis、原子顺序和 PBC-equivalent 原子坐标。

## 独立物理几何门

R2 不信任 input metadata 对物理幅度的自述，而从父/派生 STRU 独立重建：

- `A_angstrom=A_dimensionless*LATTICE_CONSTANT_bohr*0.529177210903`；
- 应变满足 row-lattice 约定 `A'=A F^T`，独立反解 `F` 和 `det(F)`；
- 应变点 Direct 坐标必须保持不变；位移点的 stationary atom 与晶格必须不变；
- Al 2×1×1 base、7 个 signed pair、实际 sign/axis/amplitude 必须与注册矩阵逐点一致；
- Cartesian 位移误差 `<1e-9 Å`，形变矩阵误差 `<1e-12`。

负回归明确拒绝把 `0.010 Å` 当成 `0.010` fractional coordinate 所产生的 cell-scaled 位移。

## 完整重解析与证据门

- STRU：atom count/order、物理晶格、体积；KPT、pseudo SHA、由 pseudo zion 独立得到的电子数均与冻结 manifest 一致。
- cube：origin、`n*step=A`、density/potential 几何一致、atom order、PBC minimum-image、17 位有限数值；`Ngrid` 使用精确 STRU 体积独立积分，相对误差 `<1e-10`。
- raw log：重新解析 12 个有限 Mermin 标签和 6 个恒等式；`m<=0`、`F<=E_ec<=U`，恒等式残差 `<1e-8 eV/atom`。
- force：final block 恰有 N 行并按 STRU species/atom 顺序；stress：final `3×3`、有限、对称，且 `trace(stress)/3` 与打印 pressure 一致。
- 覆盖：1 个 Al base、7/7 signed pairs、15/15 唯一 accepted runs；中心差分仍为 diagnostic-only，不声明 G4。
- raw state：analysis 保存确定性的 source→frozen SHA-256 manifest；validator 逐字比较外部 state 与提交证据。科学 summary 不写当前时间，保证同一 state 可字节级重生。
- runtime：如实登记本轮使用旧 binary `2d68a57c...`，不同于 R4 relocated binary `438c74b9...`；引用既有 6 点 storage-exact 科学等价闭包，不声称 runtime byte identity。

## 命令

```bash
/usr/bin/python3 -s scripts/analyze_s1_g1_displacement_strain_reference_r2.py --write
/usr/bin/python3 -s scripts/validate_s1_g1_displacement_strain_reference_r2.py
/usr/bin/python3 -s scripts/validate_s1_g1_displacement_strain_reference_r2.py --require-committed
```
