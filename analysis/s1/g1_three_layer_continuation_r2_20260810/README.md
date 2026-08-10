# S1/G1 三层 EOS continuation R2 结果

限定总体：`rejected_al_EOS_scope`。R1 301–306 是只读 recovery source（0 个新 run）；R2 新 run 为 8 个。

R1 operational phase 仍未接受；独立 scientific P0 recovery 为 `accepted`。最终 hard-domain 只由 Al 决定，Mg 不影响 overall。

Al KS-L vs KS-NL：`rejected`，|ΔV0|=0.766018%，|ΔB0|=2.465853%，锚定最大差=7.117918 meV/atom。

Al OF-L vs KS-L：`accepted_error_portrait`，|ΔV0|=1.212667%，|ΔB0|=2.520930%。`accepted_error_portrait` 只表示误差画像完整。

Mg 是 PBE/10e KS-NL、LDA-PZ/2e KS-L、LDA-PZ/2e OF-L 的三点三曲线纯诊断，不作硬比较。

科学边界：Al 仅是登记 PP 对的 scheme+construction discrepancy / LPP suitability bound；不是 projector-only 因果实验，也不是独立第二 KS/QE。端点复查、应变/相门和完整 G1 仍未由本范围闭合。

新 NL 与 legacy KS-L 使用同冻结源码的科学等价实现，但二进制 SHA 不同；a01ac707 的 6/6 storage-exact bridge 不等于 byte-identical same-engine。
