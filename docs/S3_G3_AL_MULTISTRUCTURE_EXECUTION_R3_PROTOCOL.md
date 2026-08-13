# S3/G3 Al 多结构正式执行 R3

本 revision 重放同一预注册矩阵 `f979447b`，使用全新 ID 301–380 与独立 state。它逐字节绑定 R1 committed failure 与 R2 operational closure；R1/R2 的 ID、state 和原始结果均保持不可变且不得重试。

R2 完成 28 点后，第 229 点 worker 返回 0 并生成完整科学拒绝结果，但 runner post-worker replay 使用了旧的晶格 low-G 选择器，在四方畸变上报告 `low-G set differs`；旧 collector 又错误地要求 worker return 与 runner terminal return 相同。R3 让 worker 与 replay 都调用同一登记的几何相关 23 函数构造，并把 `worker_return.json` 与 `runner_return.json` 分开冻结，使 worker 成功、runner 后处理失败的信封可以被 committed validator 重放。

R2 唯一运行语义修订是碰撞识别：仅把 `python*` 进程 argv 中精确出现登记 runner/worker 脚本 basename 的进程认作本工作流；监控 shell 即使命令文本提到工作流路径也不算 solver。真实第二 runner/worker 若与 CPU 74/150 相交仍 fail-closed。科学矩阵、23 函数候选、40³ 网格、20 meV 能量门与其余全部验收门保持不变。

20 个 Al 晶胞几何分别执行一个完整网格 WT 数值参考和三个 23 函数系数初值。每点在 40³ 网格上重新构造固定 23 函数空间并自洽；每步系数路线仍重建完整网格并调用相同 Hartree、PBE、局域赝势和 WT/FFT 算子。

系数路线硬门为电子数、非负积分、最小密度、投影梯度、有限差分梯度、accepted-step 能量单调、三初值能量差、相对完整网格密度 L2、变分下界 `E_coeff-E_grid >= -0.1 meV/atom` 和严格 `<20 meV/atom` 能量差。完整网格 Euler 残差保留为诊断。任一科学门失败仍完成 80 点并形成 evidence-valid rejection；运行时、解析或证据链失败立即写 `failed_no_retry` terminal，余 ID 保持未尝试，collector 仍逐点重放此前 accepted 前缀。

所有 attempt、command、preflight、stdout/stderr、return、result、density、session 和 terminal 均进入逐 ID SHA 分母；collector 与 committed validator 从 raw density 重算能量、组分、应力、电子数、梯度、FD 与轨迹。该 revision 最多关闭 Al 20 结构子门，不关闭完整 G3，不授权 S4，也不改变 G2c 性能拒绝。
