# S3/G3 能量验收 policy R2

本 revision 按用户明确授权，将 S3 fixed-WT coefficient R1 的相对完整网格 WT 能量差硬门从严格 `<10 meV/atom` 改为严格 `<20 meV/atom`。旧 R1 evidence、summary、V5.6 拒绝和 29 点 state 均保持只读，不删除、不回写、不重跑。

除这一项外不放宽任何 coefficient 路线门：电子数、负密度、最小密度、投影梯度、有限差分、接受步单调性、三初值一致性、密度 L2、变分下界、压力差和压力平台均按 R1 原值重放。full-grid WT 的 Euler 残差继续完整报告，但在本 revision 中只作为参考求解器诊断，不作为 23 函数 coefficient 路线验收门。

若 13/13 coefficient 点在新门下全部通过，处置为 `accepted_s3_al_v100_coefficient_pilot_r2`。该结论只接受 Al 单原子 V100 pilot，不关闭完整 G3，不授权 S4，不改变 G2c 性能拒绝，也不声称 Mg、20结构或120次自洽已经覆盖。下一步是以新 revision 扩展 S3 多结构矩阵。

Git 拓扑为 base→implementation→config-only preregistration→output-only evidence；全程新 solver 为 0，validator 必须从冻结 R1 `runs.json`/`summary.json` 逐项重算。
