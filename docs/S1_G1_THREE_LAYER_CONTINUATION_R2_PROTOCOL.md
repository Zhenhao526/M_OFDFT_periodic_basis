# S1/G1 KS-NL 三层 EOS 不可变恢复与 continuation 协议（R2）

协议版本：`S1-G1-THREE-LAYER-CONTINUATION-20260810-R2`。实现基线：`439bf90049551f2b54bf6a17e0fb8689f5dfb100`。

## 1. R1 的不可变处置

R1 的 301–306 六个 solver 点均写出 `runner_return.return_code=0`、`result` 与 `accepted`，但宿主/SSH 编排在最后一个 accepted fsync 后、`phases/p0.json` 写入前中断。R1 没有 phase、barrier 或 terminal；因此 operational 状态固定为 `incomplete_missing_phase_marker_indeterminate_late_orchestration_disconnect`，不得伪称 R1 phase accepted。R1 state 永不修改；301–306 不重跑；307–318 永久不执行。

本协议在全新的外部 state 中对 301–306 原始字节作一次性 scientific recovery。正式 recovery 必须逐点重解析 PP/header/projector、日志标签、压力/应力迹、cube 原点/轴/原子/积分、`eig_occ.txt` 的电子占据总和与最高 band 占据、NBANDS、`warning.log`、4-rank affinity，并绑定 attempt/accepted/result/runner/session 与全证据 SHA。它必须重算全 R1 state 的 151 文件、84,625,288 字节和绝对路径 sha256sum-list digest；同时确认 R1 phase/barrier/terminal 仍不存在、307–318 无任何 attempt/run/accepted。

recovery 通过只生成独立的 `r1_p0_recovery.json`，不回填 R1 marker。该 barrier 同时写入外部 R2 state 和版本化路径 `orchestration/s1/g1_three_layer_continuation_r2_20260810/r1_p0_recovery.json`，两份必须逐字相同并单独提交。327–334 runner 只接受 clean HEAD 中被 Git 跟踪且与外部字节同 SHA 的 accepted recovery barrier。

## 2. 新 formal attempts

新 ID 只有 327–334；任何 ID 至多一次尝试，失败后不得删除、覆盖或同 ID 重试。

- 327–332 是原 R1 307–312 的 Al PBE/3e KS-NL EOS 映射：0.90、1.10、0.94、0.97、1.03、1.06，均为 40/160 Ry、28³；与 recovery source 301 的 1.00 合成七点。
- 333–334 是 Mg PBE/10e 0.90/1.10 点，84/336 Ry、24×24×16；与 source 304 合成三点 compatibility/convergence diagnostic。
- 301–306 贡献 recovery source evidence，贡献 0 个 R2 新 run；不得复制成新 ID 或复用 attempt state。

运行固定 node01 物理 core 30–33、4 MPI ranks、每 rank 1 thread；`pe-list=30,31,32,33:ordered`、bind-to-core。每点 fresh start。外部 R2 state：`/home/shenwei01/.local/state/m_ofdft/s1_g1_three_layer_continuation_r2_20260810`。

每点硬验证包括：solver RC=0、唯一 SCF converged、有限 F/m/U/E_ec/压力、`m<=0`、`F<=E_ec<=U`、`E_ec=F-m/2`；UPF SHA/header/zval/projectors；`zval*nat=expected Ne=log Ne=cube integral=eig_occ weighted sum`；cube 原点为零且轴/原子与 STRU 一致；NBANDS 与登记值一致且最后 band 最大加权占据 `<1e-8`；应力迹与总压差 `<1e-5 kbar`；4-rank affinity 精确为 core 30–33；warning 无 fatal/nonconvergence/nonfinite 标记。

阶段锁：accepted recovery barrier 后才能启动 `al_eos`；`al_eos` 6/6 phase marker 后才能启动 `mg_required`。runner 忽略 SIGHUP，stdout/stderr 失败不得阻断 phase marker；每个 attempt marker 用 `O_EXCL` 在 solver 前写入。任一 ID failure/timeout 即保全并停止，不重试。

## 3. 科学验收和范围

P0 recovery 重新计算严格门：每材料 high/extra 相对 normal/extra 的 `|ΔE_ec|<1 meV/atom`、`|ΔP|<0.02 GPa`，normal/extra 相对 normal/common 的 `|ΔE_ec|<2 meV/atom`。R1 当时的 operational barrier 联合依赖 Al+Mg；本 R2 最终 overall 则只由 Al hard-domain 决定，Mg 永远只作 diagnostic。

Al 三条七点曲线各自以 1.00 锚定并做 BM3：KS-NL=327/329/330/301/331/332/328；KS-L=冻结 R4 quarter-smearing 047/073/074/043/075/076/049；OF-L=071–077。KS-L↔KS-NL 门为 `|ΔV0|<=0.5%`、`|ΔB0|<=10%`；OF-L↔KS-L 门为 `|ΔV0|<=1%`、`|ΔB0|<=10%`，失败时只可记 `accepted_error_portrait`。每条 BM3 最大残差 `<1 meV/atom` 且 V0 位于采样区间。

legacy Al 必须逐 ID 核 result/STRU/INPUT/KPT/local PP Git identity与SHA、PBE/3e、KS-L quarter smearing/28³、KS-L/OF-L 使用同一 `al.gga.psp` 和同七体积映射。新 NL 逐点反绑 manifest material/role/cutoff/kmesh/Ne 与完整 runtime identity。

Mg 同时报告三条纯诊断曲线：PBE/10e KS-NL、LDA-PZ/2e KS-L、LDA-PZ/2e OF-L；任何差值、fit 或 coverage 都不进入 overall。新 NL 二进制 SHA 为 `2d68…`，legacy KS-L relocated 二进制 SHA 为 `438c…`；`a01ac707` 仅证明 6/6 storage-exact bridge，口径是同冻结源码的科学等价实现，不是 byte-identical same-engine。

Al 的解释只允许“登记 PP 对的 scheme+construction discrepancy / LPP suitability bound”，不得称 projector-only 因果差或普适 LPP 上界。即使全部通过，本轮也只关闭 Al 七点 EOS pilot；端点 cutoff/k、应变/相门和独立第二 KS/QE 仍由后续协议处理，G1 不得仅因此报 6/6。

## 4. 提交与最终验证

先提交实现，再提交生成输入的 preregistration。正式 recovery 后单独提交版本化 barrier，solver 只能在该 clean commit 上启动。最终 evidence/analysis 另行提交；validator 以 `--require-committed` 在 clean tree 上逐字重放。禁止编辑共享进度文档、禁止合并或推送，等待主任务审阅。
