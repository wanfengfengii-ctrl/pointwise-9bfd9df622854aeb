# Readlift

北湾基因组研究中心的离线成对读段加工库。上游提供源/目标参考、审核后的固定有向映射及成对源比对；数据工程师固定任务、执行转换，向覆盖加工交付目标区间和配对资格，向变异加工交付路径、MD/NM，向质控交付逐事件来源账目。

仅 Python 3.11 标准库，无安装步骤。在项目根目录运行：

```sh
python3.11 -B -m unittest discover -s tests -v
python3.11 -B verify.py
python3.11 -B verify.py --demo samples/day.json
```

demo 使用测试临时目录，展示真实加工和关闭重开。可追加 `--store ./day.archive.json` 保留成果；父目录须存在。再次执行不覆盖用户目录，相同输入幂等，已完成任务不重算。运行清单为 `.pointwise-runtime.json` 和 `run-manifest.json`。测试仅使用项目样例及独立临时目录。

## 一天的业务

1. 用版本ID和命名碱基登记源、目标参考，形成不可变参考目录。
2. 登记审核映射，验证双行消费、方向、区间和两侧不重叠，形成固定映射及消费摘要。
3. 整批接纳成对源比对，核对身份、查询及参考消费；后部错误拒绝整批。
4. 用精确批次、映射和显式配对配置创建任务，冻结版本摘要与配置副本。
5. 执行固定映射路径组合，保留查询事件、源位置及块列身份，按实际目标连续性分段。
6. 装配完整序列质量、软剪切、MD/NM、主段与配对字段，核对批次来源账。
7. 原子发布绑定、分段、记录、核账与完成回执；失败任务可重试。
8. 读取、导出并关闭重开，供覆盖、变异加工使用同一固定成果。新增输入不改变旧成果。

`reference/` 拥有参考及映射，`intake/` 拥有完整源批次，`projection/` 拥有任务及转换事件/分段，`assembly/` 拥有交付记录及核账，`archive/` 承载原子快照，`service.py` 协调业务。读取由 `results.py` 构造独立视图，`export.py` 写稳定JSON，`requests.py` 复制入口容器。24个业务模块均接入公开入口。

## 公开 API

`from readlift.service import ReadliftService`，用 `ReadliftService(path)` 或上下文管理器打开调用方指定的本地快照：

- `register_reference(version_id, sequences)`：sequences是名称到非空大写ACGTN的字典，返回固定版本数据。
- `register_mapping(mapping_id, source_version, target_version, blocks)`：返回固定映射、摘要和消费汇总。
- `ingest_pairs(batch_id, pairs)`：返回完整不可变批次数据。空列表合法。
- `create_job(batch_id, mapping_id, pairing)`：返回内容决定的稳定任务ID。
- `execute_job(job_id)`：返回已提交成果字典；完成任务重放直接返回原成果。
- `read_result(result_id)`：返回独立成果字典，不做转换。
- `export_result(result_id, target)`：向具有write(str)方法的目标写出规范JSON，返回None。

参考、映射及批次同ID同规范内容幂等，同ID异内容抛ValueError且旧内容不变。输入顺序不影响块的源路径排序、批次的成对排序和两端排序。任务必须使用与映射源版本一致的批次。版本、映射、批次、pair_id、read_id及序列名称均为非空字符串。缺引用抛KeyError；非法值、操作、区间、方向、跨度、消费及身份冲突抛ValueError。文件系统和导出异常原样传播；CLI打印error并退出2。测试失败退出1。

`samples/day.json` 是完整可调用数据。pair形状为 `{"pair_id": "...", "reads": [mate1, mate2]}`。每个read严格包含read_id、pair_id、mate_index、source_version、source_name、start、source_strand、operations、sequence、quality。每对恰有mate_index整数1和2；批次内read_id及pair_id不重复。质量是完整序列等长的可打印ASCII，反向只反转质量。

每块严格包含block_id、source_name、source_start、source_end、target_name、target_start、target_end、strand、source_aligned、target_aligned。所有坐标为零基半开整数区间，单位为碱基。strand为整数1或-1。对齐行等长，允许-，不允许双缺口列，两侧各消费至少一碱基。源去缺口行等于源区间正向碱基；目标去缺口行等于目标区间碱基，负向时使用反向互补。真实替换合法。块ID唯一，两侧同序列块不能重叠，允许空映射、间隙、重排和方向变化；执行时每个源参考消费位置必须有覆盖。

pairing必须完整包含mate1_strand、mate2_strand（1或-1）、order（mate1_left、mate2_left、either）、min_span及max_span。跨度为整数且0 <= min_span <= max_span；没有隐式默认配置。

## 路径、分段与差异口径

源操作为正长度M/I/D/S，相邻同类合并，S仅两端。M/I/S消费查询，M/D消费源参考，查询消费等于完整序列及质量长度，源消费为正且不越界。M不保证匹配。负向源操作按反向互补查询、反向质量在递增源坐标解释，保留原始序列。

组合严格沿指定块列，不搜索或重新比对：

| 源读段事件 | 双侧映射列 | 源独有列 |
| --- | --- | --- |
| M | M | I |
| D | D | 不生成操作，保留来源 |

读段I保留为I；目标独有列生成D；源S只作剪切。读段源跨度[s,e)只纳入s < anchor < e的目标独有列，端点不生成首尾D。同锚点按读段I、目标独有列、右侧源事件输出。读段I归属右侧源事件的片段，末端I归属左侧片段；目标独有列保留所属块，源独有事件随其源片段。每个查询插入身份仅出现一次。

相邻目标事件只有同序列、同方向且按该方向连续才合段；块边界本身不强制拆段。无目标的片段不能伪造坐标；无查询的纯删除片段只留来源账，不独立交付。整条读段无法形成含M段时拒绝成果。分段query_start/query_end使用原始完整序列坐标，包含M和I，排除S，区间互不重叠。

交付方向为源方向乘映射方向。每段保留完整序列、质量，负向交付反向互补和反转质量；目标操作按递增目标坐标输出，其余查询碱基软剪切，负向同步交换剪切侧。主段按M+I最多，同分取最早源路径；其余supplementary=true。记录按pair_id、mate_index、源路径段序稳定排列。

MD/NM从目标参考和交付操作重算。M按字面比较，N/N匹配，N/其他错配。MD包含匹配数、目标错配碱基、^目标删除碱基；I/S不推进目标，D不推进查询。NM为错配+I+D，S不计入。目标消费必须等于目标区间长度，完整查询消费必须等于序列长度。

配对字段引用对端主段。仅两端各一个交付段、同目标序列、满足配置方向/相对位置及闭区间跨度时proper_pair=true。任一端分段取消整对资格。模板跨度用两主段外包区间；左端为正、右端为负，同起点mate1为正，跨序列为0。补充分段不声明正常配对。

## 成果与失败

成果包含result_id、binding、segments、records、audit、receipt。audit统计输入对数、读段数、段数、主段、补充分段、正常配对数，并逐读段保存源事件、投影事件、未交付事件及消去源删除。空批次计数全零。核账检查身份、区间、消费、正文方向、标签和配对，不通过则不发布。

本地存储是带版本和内容摘要的单一JSON快照，保存全部目录和成果/回执；同目录临时文件写入并fsync，再以os.replace提交。不提供并发写入保障。提交前异常保留旧快照并清理临时文件；提交点后不再做可能失败的计算或导出。损坏快照抛ValueError且不重置；现有目录重复打开保留用户数据。所有业务返回值与存储隔离。导出只消费已提交成果，稳定键/记录顺序，无随机数或时间戳；写入失败可留下目标前缀，成果不变，重试须使用新建或清空目标。

SnapshotStore.checkpoint(stage, candidate)是可覆写的提交前存储检查点，正常实现验证检查点名称及打开状态；测试用抛真实OSError的存储子类验证回滚，业务入口没有故障模式。

## 可复核小例和回归

- ACGT到ACAGT，映射AC-GT/ACAGT，4M得到2M1D2M、MD=2^A2、NM=1、目标跨度5。
- ACAA到ACA，映射ACAA/AC-A，ACAAA的2M1I2M得到2M2I1M、MD=3、NM=2，最后M仍源位置3。ACA的2M1D1M得到3M、MD=3、NM=0。
- ACGGT的2M1I2M沿AC-GT/ACAGT得到2M1I1D2M、MD=2^A2、NM=2，I唯一。
- ACGTAA映射ACGCCTTA，前3碱基正向至[0,3)，后3碱基负向至[5,8)：查询区间[0,3)/[3,6)，完整序列ACGTAA/TTACGT、质量ABCDEF/FEDCBA，均3M3S、MD=3；第一段主段，配对资格取消。

完整discover保留全部测试：test_projection覆盖恒等、缺口、锚点、N、固定绑定和空批；test_assembly覆盖方向、主段、跨度、跨序列、连续块、纯删除片段及覆盖失败；test_reference覆盖参考/映射拒绝及重放；test_intake覆盖整批原子性、操作、身份及任务绑定；test_archive覆盖真实存储回滚重试、隔离读取、重开、损坏拒绝、导出失败与核账守卫；test_service贯通一天剧本。手算预期不调用生产计算函数。部分测试及demo在真实断言后输出POINTWISE_SCENARIO JSON。

不支持映射搜索、重新比对、完整SAM/BAM/CRAM、变异检测、统计分析、并行调度或多写入者。
