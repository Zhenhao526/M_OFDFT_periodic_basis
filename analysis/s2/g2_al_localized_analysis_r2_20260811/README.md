# S2/G2 Al 局域扰动 108 原子 pilot

状态：`evidence_valid_scientific_gate_rejected`。

本证据以一次 108 原子 KS-NL 局域位移计算为独立参考，固定 23 函数原始候选的周期展开，检查五点几何秩连续性、八相位 eggbox、伪力、密度和算子误差。

- eggbox 能量峰峰值：0.374359197891 meV/atom
- 最大伪力：0.00836031492733 eV/Å
- 密度相对 L2：0.00467935435756
- 秩路径最小总秩：2171 / 2173
- 未通过门：eggbox_pseudoforce, full_effective_rank

本 pilot 不关闭低 q 响应、Mg、G2c 或 G2 总闸门。

## Analysis-only recovery

R1 solver raw was accepted after strict 108-atom cube replay; no solver was rerun. The R1 single-atom parser failure remains preserved.
