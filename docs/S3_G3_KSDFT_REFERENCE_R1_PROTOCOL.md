# S3/G3 Al KSDFT 主参考对照 R1

本 revision 修正参考层级，但不改写 S3 fixed-WT R1 的任何历史文件。KSDFT 是密度、压力与锚定相对能量的科学硬参考；完整网格 WT 只保留为同泛函数值、网格和求解器收敛诊断。不同泛函或赝势之间的绝对总能禁止直接比较。

正式新增三个 KS-NL 点 `S3-20260812-030..032`，体积比为 `0.995/1.000/1.005`，使用与冻结 Al 标准参考相同的 PBE、PseudoDojo `Al_std.upf`、40/160 Ry、28³ k 点和有限展宽口径。旧 S3 coefficient 点 `017/020/023` 及全部 R1 state 只读，不重跑。

科学量定义如下：KS 密度周期 Fourier 重采样到 40³ 后，与相同体积的 coefficient 密度逐点比较，不允许平移拟合、重新归一或相位优化；能量只比较相对 `V/V0=1` 锚定后的曲线；压力由两路线各自在 `0.995/1.005` 两端的自洽总能中心差分得到。KS `V0` 直接应力只作交叉诊断。

硬门保持用户登记值：电子数误差 `<1e-10`、密度 L2 `<1.5%`、锚定相对能量差 `<10 meV/atom`、压力差 `<0.2 GPa`。`V0` 新 KS 点还必须复现历史 301 的能量 `<1 meV/atom`、压力 `<0.02 GPa`。任何运行或解析失败均终止 revision、冻结 `failed_no_retry` terminal，余 ID 不执行；科学门失败则三点仍完整执行并形成 evidence-valid rejection。

Git 拓扑固定为 base → implementation → config-only preregistration → output-only evidence。正式执行前必须独立审计通过；正式 state 使用 O_EXCL/原子发布，禁止重试旧 ID。
