# 两阶段固定参考转换（公开增量契约）

现有业务、输入字段、单步消费及运行方式见README.md。V1的create_job和全部旧成果保持原样；本题只增加双映射任务，继续使用ReadliftService、execute_job、read_result和export_result。chain_cases.py提供有限手写数据及预期，并没有链执行实现。

## 入口与固定绑定

新增ReadliftService.create_chain_job(batch_id, mapping_ids, pairing)，返回稳定任务ID。mapping_ids须为恰含两个非空字符串的列表，依次解释为A→B和B→C；版本ID必须相等才算中间闭合，序列文本相同不能代替版本身份。批次中每个读段的source_version须为A。重复映射ID及重复版本在满足闭合时合法，空批次沿用V1口径。pairing完整沿用原配置。

创建时冻结批次、两个有序映射及三个有序参考的身份和内容摘要，复制配置与输入列表。同绑定重放返回同ID，不登记临时批次、临时参考或临时映射；不同绑定不得混用任务或成果。链binding公开包含job_id、batch_id、batch_digest、mapping_ids、mapping_digests、reference_versions、reference_digests、pairing；mapping_ids和mapping_digests各含两项，reference_versions和reference_digests各含三项，均按链顺序保存。可增加不冲突的字段。缺引用抛KeyError；非法列表、配置、批次版本或不闭合链抛ValueError，创建失败不新增任务。旧库不需要重写或回填。

## 路径语义

严格沿两次审核列运输，不搜索、不重新比对，也不按字符相同把不同来源事件匹配。每阶段沿用V1的M/I/D/S、源独有列、目标独有列、严格内部锚点和插入归属规则。阶段一M成为I后仍是同一个原始查询事件；阶段一新产生的D没有查询身份，遇阶段二源独有列即消去；原始查询I即使与阶段二新增目标碱基同字符、同锚点，仍分别是I和D。

第二阶段的内部锚点以第一阶段同序列、同方向、连续目标消费的各个跨度为准；不能跨中间跳跃、方向断点或不同序列虚构跨度。纯D跨度也须继续运输，不能因中间不交付记录而丢弃；每个被运输的中间参考消费位置，包括纯D位置，都必须有第二映射覆盖。第一阶段没有可归属目标跨度的事件保留为未交付来源，不伪造坐标。阶段一已消去的事件不复活。

第二阶段消费方向按中间跨度解释，但最终路径次序仍沿原始源路径，累计映射方向为两阶段方向之积。原始完整查询下标、序列、质量和read_id始终不重编号；中间记录的软剪切不是新的原始S。目标消费事件按最终同序列、同累计方向及连续性重新分段，中间段界不是永久屏障；无目标事件的归属继续使用V1规则。最终纯D段不独立交付，但删除事件可参与最终有查询段。最终整条读段没有含M交付段、任一阶段缺覆盖时整次拒绝，不提交部分读段。

最终segments和records沿用旧字段含义、排序、软剪切、主段、MD/NM和配对规则，只基于最终段集及C参考计算；不累加阶段NM，不沿用中间proper_pair。

## 可导出的阶段证据

 audit保留原计数及source_ledger。链任务的每个读段账目保存原source_events，并增加stages，恰为两个按执行顺序排列的对象，每个包含mapping_id和events。每个events是按原始路径次序排列的事件列表，event_id为该阶段从0开始的连续序号。事件至少包含：

- event_id、operation、query_index、source_event_id、parent_event_id；
- source_name、source_position、anchor、target_name、target_position、mapping_strand、block_id、column；
- fate、next_event_id、segment_index。

source_position、anchor、块列和目标坐标描述本阶段列消费，同名字段沿用V1语义；source_name是本阶段输入参考的序列名，query_index始终是原始完整查询下标，mapping_strand是累计映射方向。第一阶段source_event_id及parent_event_id指原source_events的event_id，新增目标D二者为null。第二阶段parent_event_id指第一阶段事件，source_event_id继承原始身份；第二阶段新目标D二者为null。原S仅保存在source_events；其他每个原事件在第一阶段恰有一个对应事件。映射产生的D即使后续消去也须留下阶段一证据。

第一阶段事件的fate为continued、elided或undelivered；continued的next_event_id引用唯一第二阶段子事件，其他为null。elided表示操作已消去，undelivered表示无可归属中间目标跨度；第一阶段segment_index为null。第二阶段fate为delivered、elided或undelivered，next_event_id为null；delivered的segment_index指同读段最终段号，其他为null。被消去的D用operation=null记录其实际消去列；不能删除该证据。最终segments.events中每个事件须带event_id，引用同读段第二阶段事件。允许增加其他证据字段，但上述字段必须能独立串起原始事件、两次块列及最终归属。

核账须检查原始身份不丢不重、父子引用及去向闭合、逐阶段实际列消费、最终事件单一归属，以及最终记录、标签和配对与这些证据一致；不能仅附上一份未经核对的日志。阶段账、最终记录及完成回执一起沿原发布边界提交。空批次source_ledger为空，所有计数为零。

## 手算输入及运行

运行python3.11 -B chain_cases.py fixtures验证手写输入可被现有参考、映射和接纳校验接受，不执行新增能力。probe-v1模式只观察直接复用V1中间交付记录的行为，不是链功能验收。verify和demo模式供实现后核账及重开演示，verify失败通过非零退出码保留。它们不代替全部原测试或交付者的独立核账。

- cancel：ACGT经AC-GT/ACAGT再经ACAGT/AC-GT，4M最终仍为4M、MD=4、NM=0；中间独有A留下产生、消去两阶段证据。
- independent：原ACGT的查询ACAGT、2M1I2M，第一步恒等，第二步AC-GT/ACAGT；最终2M1I1D2M、MD=2^A2、NM=2。
- double_reverse：ACGTAA→TTACGT→ACGTAA，两次全长负向，最终6M，完整查询及质量恢复原交付方向。
- rejoin：第一步把ACGTAA的ACG送到中间[0,3)正向，TAA送到[5,8)负向；第二步送回最终[0,3)正向和[3,6)负向。最终每端一条6M，MD=6、NM=0，可正常配对。still_split把最终后半段改到[5,8)，保留两段及正确原始查询区间，不能正常配对。
- deletion_bridge：源ACGT、中间AGCT、最终ACGT，两次分别交换C/G的单碱基映射；查询AT、1M2D1M。中间只有首尾M各自可交付，中间两个独立D不得丢失；最终1M2D1M、MD=1^CG1、NM=2，并重新合为一段。第二步若缺中间G的覆盖，整次仍须拒绝。

所有输入由chain_cases.py直接构造，样例第二端使用原始反向查询和反向质量；预期均按上述列关系手写，不调用链计算作为参照。

## 失败、兼容及交付

沿用ValueError、KeyError及文件系统/导出异常传播。执行拒绝或提交前失败不新增成果或完成回执，已创建任务可重试，既有目录及成果不变。SnapshotStore.checkpoint的candidate_result、candidate_receipt、before_replace可用于真实OSError发布失败核验；这不是业务故障开关。

允许全量扫描、完整事件展开及复用原快照事务，无新性能限制。返回容器修改、登记其他映射及关闭重开不能改变绑定或成果；读取、导出与完成任务重放不得重新运输。保留全部原接口、原回归字节和旧成果。范围不含路线搜索、任意长度链、重新比对、完整SAM/BAM、并发写入或崩溃恢复。

交付简短交接不变量说明及独立小例核账，验证两次方向变化、重新合段、独立插入、未交付删除、覆盖拒绝、最终无M、发布失败和旧行为。更新现有两份运行清单，使verify实际检查全部旧回归和新链结果，demo展示链任务、阶段证据及重开导出；保留.pointwise-runtime.json的version、kind、setup及argv形式，不把仅打印成功当核验。原运行命令及chain_cases.py verify/demo应保持可直接调用。
