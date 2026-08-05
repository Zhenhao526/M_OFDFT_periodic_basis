# M-OFDFT 扩展文献逐篇研判

> 版本：V1.0（2026-08-05）
> 精读对象：扩展包 13 篇全文、1 篇网页全文；并与原参考包 8 篇核心文献交叉核对
> 评判原则：把“直接证明本路线”“只证明某个模块”“邻近方法类比”严格分开

## 0. 先给结论

扩大调研后，原项目的**技术可行性略有上调，宽泛创新性明显下调**。

1. 周期 Al/Mg/Al–Mg 的 OFDFT、构型力和晶胞优化已有成熟数值先例，说明金属基线、固定 KEDF 变分求解及力学闭环不是空想。
2. 2014 年 AMD-OFDFT 已在周期 Ti 中实现原子中心球内密度展开、变分优化和 Pulay 力。因此“原子中心密度 + OFDFT + Pulay 力”不能再作为笼统的首创主张。
3. 尚未发现工作同时完成“全空间系统可收敛的原子/低 G 混合价密度基 + 同 KEDF 平面波极限验证 + 连续规范 + 原子力和晶胞应力 + Al/Mg”。本项目仍可能在这一收窄后的组合上创新。
4. 现有金属 ML-KEDF 结果不足以支持“普适、自洽、可迁移且整体超过 WT/WGC”的承诺。S5 必须保持为闸门后的独立高风险分支。
5. 显式 Gaussian 与 PW 系数直接拼接有严重线性相关和性能负证据。优先路线应是互补投影或长短程算子分离，并用端到端性能闸门淘汰无收益方案。

## 1. Das, Iyer & Gavini 2015：Al/Mg/Al–Mg 实空间 OFDFT

**文献**：S. Das, M. Iyer, V. Gavini, “Real-space formulation of orbital-free density functional theory using finite-element discretization: The case for Al, Mg, and Al-Mg intermetallics,” *Phys. Rev. B* 92, 014104 (2015). <https://doi.org/10.1103/PhysRevB.92.014104>

**研究问题**：能否把含密度依赖非局域 KEDF 的 OFDFT 局域化为实空间有限元变分问题，并统一计算原子位置和晶胞几何的构型力。

**方法**：把静电和非局域动能中的扩展相互作用写为辅助势场的局部变分问题；采用有限元离散和构型力；主要使用 WGC KEDF 与 bulk-derived local pseudopotentials。

**关键量化证据**：

- fcc Al：OFDFT-FE 的平衡原子体积 15.68 Å³、体模量 81.7 GPa；PROFESS 为 15.68 Å³、81.5 GPa。
- hcp Mg：OFDFT-FE 为 21.40 Å³、36.8 GPa；PROFESS 为 21.43 Å³、36.6 GPa。
- Al 位移恢复力为 0.148 eV/Bohr，PROFESS 为 0.137；Mg 均约 0.019 eV/Bohr。
- 周期空位形成能在约 108 原子已近收敛；若要求局域缺陷引起的电子扰动在边界消失，则需要约 864–1000 原子，说明缺陷对长程密度表示远比体相苛刻。

**局限**：有限元基不随原子中心移动，没有本项目的原子密度基 Pulay、显式低 G 冗余和规范不唯一问题；WGC 的稳定性也不能由少量成功体系外推。

**对项目的影响**：S1、S3 和固定晶胞/晶胞力学的物理闭环可行性提高；S2 的“紧致基能否覆盖缺陷的长程响应”仍无直接答案。S6 不应在只通过小胞体相后自动启动。

## 2. Rufus & Gavini 2022：原子中心增广有限元的力与应力

**文献**：N. D. Rufus, V. Gavini, “Ionic forces and stress tensor in all-electron DFT calculations using enriched finite element basis,” arXiv:2205.07161. <https://arxiv.org/abs/2205.07161>

**研究问题**：当有限元空间加入随原子移动的数值原子增广函数后，如何得到变分一致的原子力和应力。

**方法**：把原子位移和晶胞形变视为生成器，推导构型力；显式保留增广函数中心移动导致的额外项，其作用等价于本项目必须处理的 Pulay 类贡献。

**关键量化证据**：

- CO 的解析力与有限差分差约 (2.5\times10^{-6}) Ha/Bohr；
- SiC 双空位约 (3.3\times10^{-6}) Ha/Bohr；
- 金刚石应力差约 (1.1\times10^{-6}) Ha/Bohr³。

**局限**：全电子 Kohn–Sham DFT，不是 OFDFT 密度系数变分；没有低 G 显式通道、正密度约束或 ML-KEDF。

**对项目的影响**：证明“随原子移动的增广基 + 完整力/应力”在数学与工程上可以达到很高有限差分一致性。G4 应采用构型力/生成器框架，而不是只依赖自动微分；AD 可作为独立实现的导数 oracle。

## 3. Tan, Pickard & Witt 2023：PROFESS 自动微分

**文献**：C. W. Tan, C. J. Pickard, W. C. Witt, “Automatic Differentiation for Orbital-Free Density Functional Theory,” *J. Chem. Phys.* 158, 124801 (2023). <https://doi.org/10.1063/5.0138429>

**研究问题**：自动微分能否简化 OFDFT 的力、应力和高阶性质计算。

**方法**：建立可微分的 PROFESS-AD，并通过 EOS、几何优化、声子和弹性常数比较 AD 与有限差分/应力应变方法。

**关键量化证据**：

- bcc Li 的 AD 与有限差分声子色散一致；
- fcc Al 的 OFDFT-AD 弹性常数 \(C_{11},C_{12},C_{44}=101,62,35\) GPa，应力–应变为 101,63,35 GPa；
- 对最高 1000 原子的 fcc Al，导数相对能量计算的额外开销不超过约 20%。

**局限**：网格/平面波自由度固定。自动微分会忠实微分一个不连续的硬裁剪或变秩算法，但不能把它变成光滑方法。

**对项目的影响**：S4 的工程风险下降，但前提是 S2 已冻结连续的基组、投影器和秩。建议解析构型力为主、AD 为逐项回归、有限差分为最终审计，形成三角验证。

## 4. Xu et al. 2022：非局域赝势能量密度泛函

**文献**：Q. Xu et al., “Nonlocal Pseudopotential Energy Density Functional for Orbital-Free Density Functional Theory,” *Nat. Commun.* 13, 1385 (2022). <https://doi.org/10.1038/s41467-022-29002-3>

**研究问题**：OFDFT 没有轨道，能否仍评价标准非局域赝势的能量。

**方法**：用近似非相互作用密度矩阵泛函表达非局域赝势能；对 Li、Mg、Cs、Be、Cd、K、Zn 和 Li–Mg 测试。

**关键证据**：多种静态和动力学性质较局域赝势路线改善，并保持线性标度的目标。

**局限**：参数需要按体系/赝势调节；引入了新的近似密度矩阵泛函，不能消除 KEDF 误差；论文未给本项目核心 Al 的直接验证。

**对项目的影响**：这是 LPP 失效后的研究备选，不是 S1 主线。S1 应先用同一 LPP 分解实现误差与 KEDF 误差，不能为了扩大元素范围而同时引入 NLPPF 变量。

## 5. Rios-Vargas et al. 2024：有效 Wang–Teter 核

**文献**：V. Rios-Vargas et al., “Effective Wang-Teter Kernels for Improved Orbital-Free DFT Simulations,” *Phys. Rev. B* 110, 085129 (2024). <https://doi.org/10.1103/PhysRevB.110.085129>

**研究问题**：能否通过有效参考密度修正 WT 的不稳定和成键缺陷。

**方法**：分别用密度、动能或总能目标确定有效参考密度，构造 cWT 类模型。

**结果**：在 Si 多晶相上，使用有效密度可改善 WT 的相对稳定性与结合；论文同时指出适合小晶胞的参考密度未必适合大而复杂的晶胞，且 WT 类固定幂次构造不满足正确均匀坐标缩放。

**局限**：参数和晶胞/材料相关，不是普适 KEDF；主要验证对象不是简单金属 Al/Mg。

**对项目的影响**：G5 必须把材料/晶胞参数泄漏视为模型类别变化。若模型读入几何或外势，应命名为“条件密度模型”，不能用离线能量拟合替代普适 \(T_s[\rho]\) 的证据。

## 6. Lüder, Ihara & Manzhos 2024：大材料集 ML 动能模型

**文献**：J. Lüder, M. Ihara, S. Manzhos, “A machine-learned kinetic energy model for light weight metals and compounds of group III-V elements,” arXiv:2407.11450. <https://arxiv.org/abs/2407.11450>

**数据与方法**：433 个一元、二元、三元材料，包含 Li、Na、Mg、Al 等 11 元素；每个材料 18 个体积/应变点，共 7794 个结构。用晶胞平均动能密度描述符训练 GPR。

**关键结果**：随机 80/20 切分的测试 RMSE 为 (1.47\times10^{-5}) a.u.；能量–体积曲率的平均/中位相对误差约 13%/8%。

**决定性局限**：能量曲线是在 KS 密度和 KS 描述符上把 KS 动能替换为模型动能得到的后处理结果。它不是空间 KEDF，也没有对 \(\delta T_s/\delta\rho\)、自洽密度、力或压力作验证。随机点切分还可能让同一材料的相邻体积同时进入训练和测试。

**对项目的影响**：不能把“大数据低 RMSE”当作 S5 自洽可行性的证据。G5 必须按材料、晶相、组分、轨迹和体积分组切分，且把优化轨迹密度纳入训练/测试。

## 7. Manzhos et al. 2025：ML 引导解析 KEF

**文献**：S. Manzhos et al., “Machine learning-guided construction of an analytic kinetic energy functional for orbital free density functional theory,” arXiv:2502.05411. <https://arxiv.org/abs/2502.05411>

**方法**：沿用 433 材料、7794 体积点数据，构造可解释的晶胞平均解析式；特征含 \(\rho v_\mathrm{eff}\)，因此显式含外势信息。

**结果**：解析模型测试 RMSE 为 (2.21\times10^{-5}) a.u.；曲率平均/中位相对误差约 13.6%/9.5%。只对 fcc Al 和 diamond Si 展示初步自洽优化；从 KS 或 WT 近邻密度可以稳定，从强非平衡密度出发会因高阶导数和密度分母导致优化卡住。

**局限**：主体仍是 KS 密度上的后处理；自洽演示很少；无力、压力、组分外推和缺陷；显式外势依赖意味着它不是严格普适密度泛函。

**对项目的影响**：直接支持“离线准确不等于变分稳定”。G5 必须拆成 G5a（保守性、导数、自洽稳定）和 G5b（物理精度增益），先过 G5a 才能谈材料指标。

## 8. Su et al. 2026：DeePAW

**文献**：T. Su et al., “DeePAW: A universal machine learning model for orbital-free ab initio calculations,” arXiv:2603.18650 (2026 preprint). <https://arxiv.org/abs/2603.18650>

**方法**：直接从晶体结构预测 PAW/PBE 实空间密度，并用独立头预测形成能；训练集覆盖 88 元素、约 11.7 万 Materials Project 结构。

**关键结果**：密度 NMAPE 约 0.351%；不同晶系的直接能量 MAE 约 0.045–0.047 eV/atom。

**局限**：不是 KEDF、不是密度变分、没有由同一标量能量导出的力/应力；作者把力和应力列为未来工作。数据由 VASP PAW/PBE 固定设置生成，论文不同方法段还出现 117452 与 154719 两个训练规模数字，需等待版本澄清。

**对项目的影响**：削弱“通用结构到密度预测”本身的新颖性，但不取代本项目的变分 OFDFT 目标。若项目最终转成直接密度预测，必须与 DeePAW、ChargE3Net 等路线比较，不能继续以 OFDFT 闭环命名。

## 9. Ke et al. 2014：AMD-OFDFT——最重要的直接先例

**文献**：Y. Ke, F. Libisch, J. Xia, E. A. Carter, “Angular momentum dependent orbital-free density functional theory: Formulation and implementation,” *Phys. Rev. B* 89, 155112 (2014). <https://doi.org/10.1103/PhysRevB.89.155112>

**方法**：

- 在原子 muffin-tin 球内，以固定的 KS 原子参考函数展开角动量分辨密度并优化 onsite density matrix；
- 球间区保留常规网格 OFDFT；
- 引入拟合的角动量依赖非局域修正，以修复 KEDF/LPS 对 Ti 的系统误差；
- 直接在电子数约束下优化球内密度矩阵和间隙密度；
- 推导包含 Pulay 修正的原子力；双球平滑和降采样用于控制边界与几何噪声。

**关键结果**：在 Ti 的 hcp/fcc/bcc 等相中，拟合模型把常规 OFDFT 约 20% 的体积偏差显著压低；若干低能相的体积约在 1% 内。方法在 PROFESS 2.0 中实现。

**局限**：只验证 Ti，修正参数对多个 KS 性质拟合；球间仍是高截断网格，论文使用约 11000 eV 截断且没有充分端到端计时；未找到严格的解析力—多步长有限差分统计；没有晶胞应力；球内与球外并非本项目提出的“全空间原子基 + 少量低 G”。

**对项目的影响**：

- 否定宽泛首创主张：“首次把原子中心密度引入周期 OFDFT”“首次推导其 Pulay 力”均不成立。
- 提高技术可行性：系数/密度矩阵变分、球边界平滑和 Pulay 项已有原型。
- 项目创新必须收窄为：**全空间价电子伪密度的系统可收敛混合基；相同 KEDF 的严格平面波极限；连续规范和完整误差分解；Al/Mg 的原子力/应力；以及可证伪的自由度/性能收益。**

## 10. Grisafi et al. 2023：增强 SALTED

**文献**：A. Grisafi et al., “Electronic-structure properties from atom-centered predictions of the electron density,” *J. Chem. Theory Comput.* 19 (2023). <https://doi.org/10.1021/acs.jctc.2c00850>

**方法**：用非正交原子中心辅助基表示周期/分子密度，显式处理重叠度量，并从预测密度评价电子结构性质。

**关键结果**：

- 32 水分子周期胞：完整耦合模型密度 RMSE 约 3.3%，电荷分数 MAE 约 0.36%；
- 从预测密度做一次 KS 对角化，总能误差约 0.13 meV/atom，但各能量分量误差大一至两个数量级，说明总能强烈依赖误差抵消；
- QM9 大规模设置中，密度电荷 MAE 约 0.45%，总能约 1.57 kcal/mol；训练重叠/描述符可达约 10 TB、特征维数超过 (2\times10^6)。

**局限**：不是 OFDFT 自洽变分；体系主要是水和分子；一次 KS 对角化后的总能不能用来证明纯密度泛函能量准确。

**对项目的影响**：支持非正交原子密度基与度量处理，但同时要求逐分量能量验收，禁止用总能误差抵消掩盖外势/Hartree 误差。也说明原子基未必自动带来低内存。

## 11. Sun, Li & Chen 2023：截断非局域 KEDF

**文献**：L. Sun, Y. Li, M. Chen, “Truncated Non-Local Kinetic Energy Density Functionals for Simple Metals and Silicon,” *Phys. Rev. B* 108, 075158 (2023). <https://doi.org/10.1103/PhysRevB.108.075158>

**方法**：以有限个球 Bessel 函数和实空间截断表示非局域核；Al/Si 拟合后考察 Li、Mg 和 Mg–Al 迁移。

**关键证据**：截断至少要覆盖最近邻和次近邻，才可获得可靠体相曲线；过短截断会让 Al 的层错、表面和空位结果出现定性错误。体相力和 MD 可以运行，但表面弛豫仍偏离参考，作者也指出力未进入拟合。

**局限**：截断半径与材料环境相关；缺陷/表面远比体相苛刻；不是原子密度基方法。

**对项目的影响**：S2 必须加入低 G/长程响应与至少一个大胞缺陷投影 pilot。只在平衡原胞达到低 \(L_2\) 误差，不足以证明紧致基适用于 S6。

## 12. Thapa et al. 2025：实/倒空间分离 KEDF

**文献**：“Orbital-free density functionals based on real and reciprocal space separation,” *npj Comput. Mater.* (2025). <https://doi.org/10.1038/s41524-025-01643-0>

**方法**：把局域 TFW 与倒空间微扰动能修正结合，用 KS 密度傅里叶矩学习两个体系相关参数。

**关键结果**：冻结声子相对误差示例：fcc Al 0.0040，对照 OF/TFW 0.2083；hcp Mg 0.0245 vs 0.2261；hcp AlMg 0.0044 vs 0.0491。

**局限**：修正用于非自洽冻结声子计算；没有形成完整自洽总 KEDF；参数体系依赖，主要对照也不是 WT/WGC/MPN。

**对项目的影响**：强力支持保留倒空间响应信息，但不证明少量显式低 G 系数足够。G2 应把 \(q/(2k_F)\) 分层响应与真实空间缺陷同时作为验收，而非只按几何截断选通道。

## 13. Zhao et al. 2026：重求和/重整化动能泛函（网页全文）

**文献**：“Renormalization of the kinetic energy density functional,” *Comput. Theor. Chem.* (2026). <https://doi.org/10.1016/j.comptc.2026.115717>

**主旨**：梯度展开在分子/强非均匀密度上发散，Padé、Meijer-G 等重求和仍需要精确约束和稳定性控制。

**与本项目最相关的论点**：论文明确批评若模型显式读入几何，则它不是严格意义上只依赖密度的普适泛函。这与计划书已加入的 ghost-center/规范不变性问题一致。

**局限**：不是周期简单金属的数值验证，也没有原子密度基实现；只用于界定模型命名与理论边界。

**对项目的影响**：若 S5 使用元素图、核坐标或外势特征，应预注册为“特定赝势族的条件密度能量模型”；除非通过同密度、ghost 中心和不同表示的不变性测试，否则不得宣称通用 \(T_s[\rho]\)。

## 14. Remme & Hamprecht 2026：Surrogate Functionals

**文献**：R. Remme, F. A. Hamprecht, “Surrogate Functionals for Machine-Learned Orbital-Free Density Functional Theory,” arXiv:2604.20458v1 (2026 preprint，已注明投稿 *J. Chem. Phys.*). <https://arxiv.org/abs/2604.20458>

**核心概念**：不要求学习能量面在任意密度上忠实于物理泛函，只要求在固定初值、优化器、步长和迭代规则下，最小化学习标量能量能到达参考基态密度。作者提出 gradient-descent-improvement loss，使每一步到参考系数的距离至少按给定收缩因子减小，并以持久缓存自适应采样实际优化轨迹。

**关键结果**：沿用原子中心 Gaussian 密度系数；在 QM9 上，无 \(O(N^3)\) Löwdin 对称正交时密度 \(L_2\) 误差约 \(1.2\times10^{-2}\)、平均优化约 8 s，对照 STRUCTURES25 约 \(1.4\times10^{-2}\)/13 s、M-OFDFT 约 \(2.7\times10^{-2}\)/183 s。对更大 QMugs，无正交版本约 0.12/21 s，速度较好但密度误差劣于 STRUCTURES25 的约 0.068/40 s。

**决定性局限**：这是分子 QM9/QMugs 预印本；“弱 surrogate”只保证给定优化过程的基态密度，不要求正确基态能、离基态能量、力、压力或跨优化器迁移。作者把同时预测真实基态能的 strong surrogate 留作未来工作。它也不是物理 KEDF，不能进入本项目 G5b 的能量/力增益验收。

**对项目的影响**：

- 提供一条明确的失败后转向：若物理 ML-KEDF 的全局忠实性不可学，可另立“优化器条件的密度 surrogate”分支；
- 该分支必须独立命名并只以密度收敛、优化器 OOD 和端到端缩放验收，不得宣称建立通用 \(T_s[\rho]\)；
- 论文再次说明 Löwdin 正交的 \(O(N^3)\) 成本可能吞掉原子基压缩优势，支持本项目把规范/正交化端到端成本前置到 G2c。

## 15. 扩大检索中还必须纳入的证据

以下文献未全部重复下载，但已核对与项目相关的主结果；它们构成综合报告的横向证据链。

| 文献 | 证据类型 | 对项目的直接含义 |
|---|---|---|
| Lewis et al., SALTED 2021, <https://doi.org/10.1021/acs.jctc.1c00576> | 周期 Al 原子中心密度表示 | 平均 RI 密度误差可到约 0.02%，Hartree 约 0.2 meV/atom；但总静电误差约 11.6 meV/atom，必须验外势项 |
| Golze et al. 2017, <https://doi.org/10.1021/acs.jctc.7b00148> | 周期局域密度拟合与力 | 局域拟合可加速 GPW，但对象是 AO 对密度，不是 OFDFT 的独立密度自由度 |
| Ye & Berkelbach 2021, <https://doi.org/10.1063/5.0046617> | 范围分离 Gaussian density fitting | 互补/长短程分离比冗余 GTO⊕PW 更有工程希望，约 10 倍优于旧 GDF 的结果是邻近方法证据 |
| Knuth et al. 2015, <https://doi.org/10.1016/j.cpc.2015.01.003> | NAO 应力 | Pulay、moving-grid、Jacobian 和晶胞导数分项模板 |
| Zheng, Ren & He 2021, <https://doi.org/10.1016/j.cpc.2021.108043> | 赝势 NAO 应力 | 原子基 + 均匀网格 + FFT Hartree 的数值架构类比最接近本项目 |
| Doll 2012, <https://doi.org/10.1016/j.cplett.2012.03.054> | 金属局域基解析梯度 | 有展宽时解析/有限差分必须针对同一个 Helmholtz 自由能 |
| Anglada & Soler 2006, <https://doi.org/10.1103/PhysRevB.73.115122> | egg-box 与实/倒空间滤波 | G2 必须增加整胞相对网格平移的能量与伪力测试 |
| Zhuang et al. 2016, <https://doi.org/10.1103/PhysRevApplied.5.064021> | Mg/Al/合金 OFDFT | WGC 二阶密度展开可能数值不稳；WT 在相关简单金属中是不可跳过的稳健基线 |
| Sun & Chen, MPN-KEDF 2024, <https://doi.org/10.1103/PhysRevB.109.115135> | 金属自洽 ML-KEDF | 合金形成能可改善，但绝对能量/密度未一致超过 WT，且无力/压力验证 |
| Sun & Chen, ext-WT 2026, <https://arxiv.org/abs/2507.08442> | 现代约束 KEDF | 修复 WT 孤立体系下有界性并保持线性响应；应作为 G3/G5 强基线 |
| Remme et al. 2025, <https://doi.org/10.1021/jacs.5c06219> | 自洽分子 ML-OFDFT | 外势扰动轨迹与能量/梯度联合监督显著提高自洽稳定；不能外推到金属准确性 |
| Remme & Hamprecht 2026, <https://arxiv.org/abs/2604.20458> | 优化器条件 surrogate functional | 只用基态密度标签可训练收敛能量面并避开 \(O(N^3)\) Löwdin；不保证物理能量/力，只能作为独立转向路线 |
| Moldabekov et al. 2023, <https://doi.org/10.1103/PhysRevB.108.235168> | jellium/Lindhard 响应 | 正确低波矢响应与 Al 等真实材料准确性相关，G5 响应点需按 \(q/(2k_F)\) 覆盖 |
| Mi et al. 2023, <https://doi.org/10.1021/acs.chemrev.2c00758> | OFDFT 综述 | 说明 KEDF 与 LPP 是耦合但不同的两类主误差，必须分层评估 |

## 16. 逐篇精读后的共识与分歧

### 已有文献能直接支持的部分

- Al/Mg/Al–Mg 的周期 OFDFT 与 EOS/缺陷/构型力可做；
- 周期原子中心密度表示和非正交度量可做；
- 随动原子增广基的 Pulay/构型力与应力可高精度实现；
- 自动微分能显著降低高阶导数工程成本；
- 倒空间响应对简单金属 KEDF 很重要。

### 文献仍未解决、必须由本项目实证的部分

- 显式原子基与少量低 G 通道能否在同精度下稳定、连续且真正压缩；
- 低 G 通道数是否会随体积增长到失去优势；
- 同一固定 KEDF 下，系数空间极小值能否系统逼近平面波极小值；
- 基组规范、秩和投影器对几何是否可微；
- 端到端时间/内存是否优于 FFT 网格；
- 金属 ML-KEDF 能否在自洽、力、压力和 OOD 上同时不劣于强经典基线。

因此最合理的项目主线不是“直接构建通用 ML-M-OFDFT”，而是先完成一个可证伪的**表示—变分—导数**研究；ML 只在 G2/G3/G4A 通过后另立子课题。
