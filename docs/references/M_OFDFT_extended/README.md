# M-OFDFT 扩展参考文献包

> 建立日期：2026-08-05
> 调研主题：周期原子中心/混合密度基 OFDFT、变分力与应力、金属 KEDF/ML-KEDF、局域赝势和软件可实现性
> 精读结论：见 `M_OFDFT_扩展文献逐篇研判.md`；综合判断见 [`M_OFDFT_扩大文献调研与可行性再评估_2026-08.md`](../../M_OFDFT_扩大文献调研与可行性再评估_2026-08.md)

> 发布说明：仓库仅收录索引和原创研判；PDF 与逐页文本保留在本地受控证据包，公开再分发前须逐项核验许可。

## 1. 包含内容

- 本地受控证据包 `pdfs/`：13 篇可合法获取的作者版、预印本或开放获取全文（不随仓库发布）；
- 本地受控证据包 `text/`：对应的逐页文本抽取，页界以 `===== PAGE n =====` 标记（不随仓库发布）；
- `M_OFDFT_扩展文献逐篇研判.md`：逐篇问题、方法、量化证据、局限和对本项目的影响；
- 本索引：文件、来源、页数和 SHA-256。

本包是原有 `references/M_OFDFT/` 的增量补充，不重复收录原包中的 M-OFDFT、SALTED 2021、MPN-KEDF、Imoto 2021、PROFESS、Gaussian–PW 和 Mi 2023 综述。

## 2. 全文清单与校验

| 文件 | 文献与用途 | 页数 | SHA-256 |
|---|---|---:|---|
| `01_Das_2015_RealSpace_OFDFT_AlMg.pdf` | Das, Iyer & Gavini, real-space OFDFT for Al/Mg/Al–Mg；周期金属、构型力和晶胞优化直接证据 | 20 | `22f679a2d093fb2c33722d3b33935837a3dfcedbb459cc5cda46d8bedef63e5e` |
| `02_Rufus_2022_EnrichedFE_Forces_Stress.pdf` | Rufus & Gavini, atom-centered enriched finite elements；Pulay/构型力与应力方法类比 | 19 | `c9fce692a827b54b2e405df6400d5a6c3f6a9ae72e2d3b28b1bd9a45d8048038` |
| `03_Tan_2023_PROFESS_AD.pdf` | Tan, Pickard & Witt, automatic differentiation OFDFT；导数实现与二阶性质 | 15 | `2e367378eb32ab0db8a60697889799268f66cc0c7d1bbed1f0cc17da7b02f947` |
| `04_Xu_2022_NLPPF_OFDFT.pdf` | Xu et al., nonlocal pseudopotential energy density functional；突破 LPP 限制的高风险路线 | 8 | `19ae477c8e3acd563008b5ea724dee6da2a3527a010436549baea1c832ff6a65` |
| `05_RiosVargas_2024_cWT.pdf` | Rios-Vargas et al., effective/corrected WT；现代经典 KEDF 的材料依赖性 | 20 | `7cd8645c3bd57bbee9be6dec3d2b04990ff26b529c04bde2323117ba3548a605` |
| `06_Luder_2024_ML_KE_Model.pdf` | Lüder et al., bulk materials ML kinetic-energy model；大数据但非自洽 | 45 | `63eb645a907afb8feb29fc664a6b1bb82aa41593b5dfb1612a18fe7da347c75b` |
| `07_Manzhos_2025_Analytic_KEF.pdf` | Manzhos et al., ML-guided analytic KEF；后处理为主、仅小规模自洽试验 | 17 | `786623a94e8c95a52898edb0655b220ebcab78dc45322713d8a37577d2b32255` |
| `08_Su_2026_DeePAW.pdf` | Su et al., DeePAW；结构到密度/能量的直接预测邻近路线 | 33 | `1033830c55826128250acefe1a2efcdc624da618aa4108d2a13d9d3a810a8365` |
| `09_Ke_2014_AMD_OFDFT.pdf` | Ke et al., angular-momentum-dependent OFDFT；周期原子中心密度、变分优化和 Pulay 力的关键直接先例 | 16 | `95aa30c4ba70d1571b6fc392ca55d71e3a99ed3c1aae1610fa1918f9e523a1da` |
| `11_Grisafi_2023_Enhanced_SALTED.pdf` | Grisafi et al., enhanced SALTED；非正交原子中心密度基、能量传播和成本 | 11 | `f20cf622a9334df12f5b4e24f2652065135d68e9c69093ac59d71b5a2d3072df` |
| `12_Sun_2023_Truncated_NLKEDF.pdf` | Sun, Li & Chen, truncated nonlocal KEDF；局域截断对缺陷/表面的限制 | 19 | `b5a04cc4328820ea5a5d01fdd6a391a4b5b9d30a4b87191a8958d787bf02c46c` |
| `13_Thapa_2025_RealReciprocal_KEDF.pdf` | Thapa et al., real/reciprocal-space separated KEDF；低波矢/倒空间响应的重要性 | 10 | `8985d3d691c8e74a32633009e0c18d92bc505fdc9f5f99ff670dbdf4dd2cd3f5` |
| `14_Remme_2026_Surrogate_Functionals.pdf` | Remme & Hamprecht, surrogate functionals；固定优化器下只保证基态密度收敛的替代 ML-OFDFT 路线 | 8 | `78bae6ce6d4cf71bc79cfe37dfa2e5be97776dcf8205324e74d163a0ba7c1bbf` |

编号 `10` 预留给 Zhao et al. 2026 renormalization 论文。调研期间只稳定取得出版社全文页面和 SSRN 元数据，未把超时/不完整下载伪装成 PDF；其证据在逐篇研判中以“网页全文”标记。

## 3. 获取来源

主要全文来源为作者版、arXiv、PMC 或开放获取出版社页面：

- Das et al.: <https://arxiv.org/abs/1504.06368>
- Rufus & Gavini: <https://arxiv.org/abs/2205.07161>
- Tan et al.: <https://arxiv.org/abs/2212.03231>
- Xu et al.: <https://arxiv.org/abs/2201.00901>
- Lüder et al.: <https://arxiv.org/abs/2407.11450>
- Manzhos et al.: <https://arxiv.org/abs/2502.05411>
- Su et al.: <https://arxiv.org/abs/2603.18650>
- Ke et al.: <https://doi.org/10.1103/PhysRevB.89.155112>
- Grisafi et al.: <https://arxiv.org/abs/2206.14087>
- Sun et al.: <https://arxiv.org/abs/2304.03528>
- Thapa et al.: <https://doi.org/10.1038/s41524-025-01643-0>
- Remme & Hamprecht: <https://arxiv.org/abs/2604.20458>

## 4. 验证记录

- 13/13 PDF 可由 Poppler `pdfinfo` 正常解析；页数合计 241 页；
- 13/13 PDF 已抽取逐页文本；
- 抽查了首页、公式/表格密集页和结论页的渲染，未发现空白页、登录页或 HTML 冒充 PDF；
- 精读时以 PDF 页码和正文语义为准；自动抽取文本只用于检索，不替代版面核对；
- 本索引不等同于版权再分发许可。对外发布前仍须逐项核验文件许可。

## 5. 证据等级

- **A：全文精读**——本文献包 13 篇与原包 7 篇全文；
- **B：方法/结果段核读**——为补齐经典方法、软件和近年进展而核读的论文；
- **C：摘要/元数据定位**——只用于确认研究边界，不据此给出强量化结论；
- **S：官方软件资料**——官方文档、代码仓库或发布页面。

综合报告中的每项可行性结论均区分“已有直接证据”“方法类比”和“尚待本项目验证”，避免把邻近工作的成功等同于本路线已被证明。
