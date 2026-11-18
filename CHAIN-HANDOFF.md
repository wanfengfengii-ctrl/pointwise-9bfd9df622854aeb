# 链式转换交接说明（不变量与独立核账）

承接 CHAIN.md 的双映射增量。V1 业务、接口、成果字节与全部原回归不变；本说明只覆盖新增的 `create_chain_job(batch_id, mapping_ids, pairing)` 链任务。

## 不变量

1. **绑定**：`mapping_ids` 恰为两个非空字符串，依次解释 A→B、B→C；版本闭合按版本ID（`ab.target_version == bc.source_version`），序列文本相同不代替版本身份；批次每个读段 `source_version` 为 A。创建时冻结批次、两个有序映射、三个有序参考的身份与内容摘要及 pairing 副本；`job_id` 由内容决定，同绑定重放同 ID，不登记临时目录项；缺引用抛 KeyError，非法列表/配置/批次版本/不闭合抛 ValueError，创建失败不新增任务。
2. **运输**：原始完整查询下标、序列、质量、read_id 始终不重编号。阶段一完全等于 V1 投影（源独有列 M→I、D 消去、目标独有列成 D、严格内部锚点、插入归属）。阶段二锚点区间取阶段一同序列、同方向、连续目标消费的各跨度，不跨跳跃/方向断点/序列虚构；每个被运输的中间位置（含纯 D 跨度位置）必须有第二映射覆盖，否则整次拒绝；阶段一已消去事件不复活；查询 I 与参考独有碱基不按字符重配（各为 I 与 D）；累计方向为两阶段方向之积；最终路径次序沿原始源路径。
3. **合段与交付**：最终按 C 参考的同序列、同累计方向、连续性重新分段，中间段界不是永久屏障；最终纯 D 段不独立交付，删除事件可参与最终有查询段；任一读段无含 M 交付段或任一阶段缺覆盖即整次拒绝，不提交部分读段。segments/records 沿用 V1 字段、排序、软剪切、主段、MD/NM 与配对规则，只基于最终段集与 C 参考重算，不累加阶段 NM，不沿用中间 proper_pair。
4. **证据**：`audit.source_ledger[read_id]` 保存原 `source_events` 并增加 `stages`（恰两个按执行顺序的对象，各含 `mapping_id` 与 `events`）。事件 `event_id` 段内从 0 连续；阶段一的 `source_event_id`/`parent_event_id` 指原始事件，新增目标 D 二者为 null；阶段二 `parent_event_id` 指阶段一、`source_event_id` 继承原始身份，新目标 D 二者为 null。`fate`/`next_event_id`/`segment_index` 闭合：阶段一 continued→唯一子事件，elided（操作已消去）/undelivered（无可归属中间跨度）无子；阶段二 delivered→最终段号，elided 以 `operation=null` 记录实际消去列，undelivered 不交付；最终 `segments.events` 引用阶段二 `event_id`。
5. **核账与发布**：发布前核账重推阶段一列消费、逐事件核对阶段二列与累计方向、原始身份不丢不重、父子引用与去向闭合、最终事件单一归属，并由最终段集与 C 参考重算记录坐标/操作/方向/MD/NM/主段/配对后比对；不通过则不发布。发布沿用 `candidate_result`/`candidate_receipt`/`before_replace` 原子边界；执行拒绝或提交前失败无成果无回执，任务可重试，既有目录与成果不变。
6. **兼容**：读取、导出、完成任务重放、关闭重开均不重新运输（`conversion_calls` 不增）；返回容器修改、后续新登记不影响绑定与成果；快照校验按 binding 形状分派，旧库不需要重写或回填。

## 独立核账（手算，不调用链实现作参照）

| 案例 | 手算要点 | 预期 | 核对 |
| --- | --- | --- | --- |
| cancel | ACGT→ACAGT→ACGT；中间独有 A 阶段一成 D、阶段二遇源独有列消去 | 4M，MD=4，NM=0；产生+消去两段证据 | `chain_cases.py verify`；`test_chain.TestChain.test_cancel_transport_and_elision` |
| independent | 查询 ACAGT 2M1I2M，恒等+AC-GT/ACAGT；原 I 与新目标 D 各自独立 | 2M1I1D2M，MD=2^A2，NM=2 | 同上；`test_independent_insertion_and_deletion` |
| double_reverse | 两次全长负向，累计方向 +1，完整查询与质量恢复原交付方向 | 6M，MD=6，NM=0 | 同上；`test_double_reverse_restores_direction` |
| rejoin | 中间 [0,3)+/[5,8)− → 最终 [0,3)+/[3,6)−，连续重新合段 | 单条 6M，可正常配对 | 同上；`test_rejoin_segments` |
| still_split | 最终 [0,3)/[5,8) 两段，原始查询区间 [0,3)/[3,6) | 3M3S 与 3S3M，取消配对资格 | 同上；`test_still_split_keeps_segments` |
| deletion_bridge | 单碱基重排两映射；1M2D1M 的中间两个独立 D 继续运输并重新合段 | 1M2D1M，MD=1^CG1，NM=2 | 同上；`test_deletion_bridge_rejoins` |
| undelivered_deletion | 2M2D/2D2M 经恒等+跳跃映射，纯 D 最终段不独立交付 | 两端各 2M，跨度 6/-6，stage2 fate=undelivered | `test_chain.TestChain.test_undelivered_final_deletion` |
| 覆盖拒绝 | 阶段二缺中间覆盖（含纯 D 位置） | 整次 ValueError，目录不变 | `chain_cases.py verify`（coverage、deleted_coverage）；`test_missing_coverage_and_no_m_rejected` |
| 最终无 M | 阶段二全部 M→I，无含 M 交付段 | 整次 ValueError，目录不变 | `chain_cases.py verify`（no_m）；同上 |
| 发布失败 | 三个检查点分别注入真实 OSError | 无成果无回执可重试；旧 V1 成果不变 | `chain_cases.py verify`；`test_publish_failure_replay_and_reopen` |
| 旧行为 | 全部原 unittest、demo 字节与成果 | 不变 | `python3.11 -B -m unittest discover -s tests -v`；`verify.py --demo samples/day.json` |

核对命令：`python3.11 -B verify.py`（旧回归+新链）、`python3.11 -B verify.py --demo samples/day.json`（V1 demo+链任务、阶段证据与重开导出）、`python3.11 -B chain_cases.py fixtures|verify|demo`、tests/test_chain.py 内全部用例。
