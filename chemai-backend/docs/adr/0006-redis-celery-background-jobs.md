# 后台作业迁移 Redis + Celery

ChemAI 的练习后障碍诊断跑在请求处理器 spawn 的 `daemon` 线程里，重启即丢、无重试；通知投递与记忆追加 fire-and-forget 且非幂等。决定引入 Redis 作为持久化作业队列（broker），非 OCR 后台作业用 Celery worker 消费；同一个 Redis 复用为共享限流/缓存；所有作业消费者做成幂等。Kafka 被否决：单节点单进程部署下，它的分区/重放/吞吐什么都不买，运维成本倒是真实的。

Status: accepted

## 已考虑的选项

- **Kafka**：否决——分区、严格有序、重放、多消费者扇出在单节点上全都用不上，还要背上 KRaft/Zookeeper 的运维。
- **扩展既有 APScheduler + `ocr_tasks` 任务表模式**（DB outbox 式队列）：既有 OCR 路径就是这么跑的，是明显的"零新基建"选项。否决作为通用机制——进程内 APScheduler 的 job store 跨重启不持久、不可重试，也给不了共享缓存与限流。
- **Redis Streams / arq（不上 Celery）**：更轻的 async 原生替代。可接受；最终选 Celery 是因为成熟度、内置重试与可见性、生态。
- **Celery + Redis（已选）**：Redis 作 broker（开 AOF 保证重启不丢）+ 缓存/限流复用。

## 后果

- 消费者必须容忍至少一次（at-least-once）投递：
  1. 通知投递需要发送前查去重键（现状 `DiscordGateway.send` / `EmailGateway.send` 无条件发送）。
  2. 记忆追加（`practice_history` / `barrier_history` 的 `append=True`）必须按 `exam_record_id` / 诊断事件去重——当前裸追加在重试时会重复记账。
  3. 障碍诊断本身已幂等（`run_llm_barrier_diagnosis` 是重聚合 `Student.barrier_type` 而非累加），只需支持重试。
- 既有 APScheduler 的 OCR 任务表路径保留；把它合并进队列是另一个后续决策。
- **无 Redis 兜底**：broker 不可达时（`kombu`/`redis`/`amqp` 连接错误），`enqueue_job` 降级为同步执行 handler——Redis 没部署或没起来不会打断请求路径；Redis 与 worker 一上线，队列自动接管。
