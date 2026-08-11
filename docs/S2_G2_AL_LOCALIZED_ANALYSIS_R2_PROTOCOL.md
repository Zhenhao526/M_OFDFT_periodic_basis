# S2/G2 Al 108 原子局域扰动 analysis-only R2

R1 的唯一 108 原子 KS-NL 求解以返回码 0 正常结束并生成完整日志、力、占据和电荷 cube；R1 随后因复用的单原子 cube 解析器把原子数写死为 1 而 fail-closed。R1 state、失败标记和原始文件保持只读，禁止重跑。

R2 不启动求解器。它逐文件重算 R1 state inventory，验证 `34` 次 SCF 的收敛标记、324 电子、16-rank 亲和性和完整 108 原子 cube，再以支持任意原子数的严格解析器恢复科学参考。解析器要求原点为零、三条 cube 轴逐分量重建登记晶胞、108 个 Al/3e 原子按周期最小像逐个匹配登记结构，并严格核对网格值分母。

恢复后的密度按 R1 预注册门执行固定 23 原始函数候选的五点秩连续性、三档秩阈值、八相位 eggbox/伪力、密度 L2、电子数以及 H/外势/XC/WT 算子误差。科学门失败仍形成 `evidence_valid_scientific_gate_rejected`，不得伪装为通过；无论结果如何，低 q、Mg、G2c 和 G2 总闸门保持未关闭。

Git 闭包固定为：R1 closure → R2 implementation → config-only preregistration → output-only evidence。最终验证器从只读 R1 raw 重建全部 11 个输出字节。
