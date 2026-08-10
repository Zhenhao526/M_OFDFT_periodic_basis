# S1/G1 固定 10 例单命令再生成协议（R2）

协议版本：S1-G1-REGENERATION-10-R2
正式分母：10 个新 ABACUS 计算；任何失败 ID 不重试。
目标：证明一组同时覆盖 Al/Mg、OFDFT 标量和 KS 稠密 k/第三 smearing 场标签的代表性结果，可以从冻结源输入通过一条命令重新生成，并由独立解析器得到同一科学判断。

## 0. R1 封存与 R2 新分母

R1 已以 `failed_no_retry` 终止：001--003 虽有科学上正常的结果，但 004 的短进程未被轮询式 argv 采样捕获，导致 runtime affinity gate 假阴性；004 已消费且 005--010 未执行。R1 的 001--010 原样封存、禁止重试，并对本子项贡献 0/10。R2 使用全新 011--020 和全新外部 state，重新形成不可拆分的 10 例正式分母；不得借用任何 R1 accepted case。

## 1. 冻结样本

机器可读分母为 config/S1_g1_regeneration_10_r2_manifest.tsv。10 例顺序固定：

| 正式 ID | 源运行 | 内容 |
|---|---|---|
| 011 | S1-20260805-071 | Al OFDFT，V/V0=0.90 |
| 012 | S1-20260805-077 | Al OFDFT，V/V0=1.10 |
| 013 | S1-20260805-092 | Mg OFDFT，V/V0=0.90 |
| 014 | S1-20260805-098 | Mg OFDFT，V/V0=1.10 |
| 015 | S1-20260807-047 | Al KS quarter smearing，V/V0=0.90 |
| 016 | S1-20260807-043 | Al KS quarter smearing，V/V0=1.00 |
| 017 | S1-20260807-049 | Al KS quarter smearing，V/V0=1.10 |
| 018 | S1-20260807-051 | Mg KS quarter smearing，V/V0=0.90 |
| 019 | S1-20260807-045 | Mg KS quarter smearing，V/V0=1.00 |
| 020 | S1-20260807-041 | Mg KS quarter smearing，V/V0=1.10 |

每个源运行同时冻结 Git tree OID，以及 INPUT、STRU、KPT、metadata、局域赝势、源 result、源原始日志和（KS 例）两个 cube 的 SHA-256。runner 在每例前后复核源树；源目录只读，不作为输出目录。

## 2. 单命令与单次语义

预注册提交后，正式入口只有：

    /usr/bin/python3 -s /home/shenwei01/M_OFDFT_g1_regen10_r1_20260810/scripts/launch_s1_g1_regeneration_10_r2.py --all

输出根固定为：

    /home/shenwei01/.local/state/m_ofdft/g1_regeneration_10_r2_20260810

启动前输出根必须不存在。launcher 按 manifest 顺序调用 10 个精确的 per-case argv。每个 ID 先通过 O_EXCL 创建 attempt marker，再创建独立 case 目录；marker 或目录已存在即拒绝运行。任一 case 失败后立即停止，保留现场，后续 ID 留在固定分母中作为 missing/skipped；R2 中不得删除 marker、替换 ID 或重试。

## 3. 计算资源

- 主机必须为 node01。
- 每例固定 4 MPI ranks，OMP/MKL/OpenBLAS 均为单线程。
- solver 启动链经 taskset 限制到逻辑 CPU 20--23；rank wrapper 再确定性地把 rank 0/1/2/3 分别固定到 CPU 20/21/22/23。
- 每个 rank 在 ABACUS exec 前以 O_EXCL 发布 proof，绑定 rank/local-rank、PID、`/proc` start time、hostname、单核 affinity、wrapper/ABACUS SHA-256 和 runner commit。runner 独立检查四个 live PID/start-time/affinity 后发布 GO；四个 rank 再各自 O_EXCL 发布绑定 proof 与 GO 的 ACK，runner 再次检查 live identity 后发布 EXEC；rank 最后复核 ACK 和 affinity 才 exec ABACUS。
- `/proc` 进程树每 0.25 秒轮询只作诊断，不再参与验收，因此短命 OFDFT 进程不会因 argv 采样竞态产生假阴性。proof/ACK 数必须各精确为 4，且运行后字节哈希不得改变。
- 正式启动前扫描实际 ABACUS 或 Python rank-wrapper 进程；其亲和性与 20--23 有交集则不创建 state、直接拒绝。允许集覆盖全机但实际 ranks 已固定在其他 CPU 的外层 mpirun 不作为冲突。
- OFDFT 每例 600 s，Al KS 每例 1800 s，Mg KS 每例 3600 s；超时终止该进程组并把 ID 判为 failed_no_retry。
- ABACUS、mpirun、Python、taskset、GNU time 和 rank wrapper 的绝对路径与 SHA-256 均由 config 冻结。

## 4. 科学比较语义

所有门均为严格小于。

OFDFT 四例：

- 收敛；
- 源/再生成绝对总能差小于 0.1 meV/atom；
- 压力差小于 0.02 GPa；
- 不要求也不允许用未注册 cube 关闭场门。

KS 六例：

- 收敛，F 与 E_ec 以及其余全部 12 个标签均存在且有限；
- F、m、U、E_ec、E_one_elec、E_localpp、T_sU、F_s、E_Hartree、E_xc、E_Ewald 的源/再生成差小于 0.1 meV/atom，mu 差小于 0.1 meV；
- 热力学恒等式残差小于 1e-8 eV/atom，m 不大于 0，且 F 不大于 E_ec 不大于 U；不作精确零温声称；
- 再生成密度独立积分的认证相对电子数误差小于 1e-10；
- 相同几何下密度 D1、D2 各小于 0.005；
- 去常数规范后的势导数 dg 小于 0.01，绝对 RMS 小于 0.005 eV。

源/再生成 cube SHA 相等仅作诊断，不是科学门。原始 stdout、resource usage 和 cube 字节也只作诊断。输入、源 tree OID、命令顺序、runtime 身份、主机/亲和性属于硬门。

## 5. 验收和证据

硬计数必须为 registered=attempted=completed=accepted=10，failed=missing=skipped=retried=0。analyzer 独立重读原始日志和 cube，产生 summary.json、points.tsv、label_metrics.tsv、10 份 case JSON、README 和外部 state 全文件 SHA-256 清单。

validator 会在临时目录再次生成整个分析树并要求逐字节一致；随后检查正式 runner commit 仍是当前 HEAD 的祖先，且 protocol/config/manifest/runner/launcher/analyzer/validator/tests 与正式运行时完全相同。只有 completion 与全部分析证据已经提交、工作树干净、--require-committed 返回 0 时，本 R2 子项才为 accepted。

本协议只关闭“固定 10 例单命令再生成”可复现性子项；它不改变 G2/G4 的精度门，也不把 10 例抽样外推为完整数据集全部重算。
