"""公开事件是运行事实的允许字段投影，不包含模型思考或工具原始参数。"""
from .contracts import AgentEvent

EVENT_NAMES = {
    'run_started': 'run.started', 'model_requested': 'model.started',
    'model_parsed': 'model.completed', 'model_failed': 'model.failed',
    'model_rejected': 'model.rejected', 'tool_started': 'tool.started',
    'tool_executed': 'tool.completed', 'tool_cancelled': 'tool.cancelled',
    'compaction_created': 'compaction.completed', 'checkpoint_created': 'checkpoint.completed',
    'completion_rejected': 'run.rejected', 'run_failed': 'run.failed', 'run_finished': 'run.completed',
}
PUBLIC_FIELDS = {
    'status', 'stop_reason', 'kind', 'finalization_only', 'attempts', 'tool_steps', 'duration_ms',
    'call_id', 'name', 'operation_id', 'error_code', 'exit_code', 'timed_out', 'checkpoint_id',
    'compaction_id', 'trigger', 'reason', 'error', 'completion_metadata', 'tool_status', 'tool_error_code',
}


def build_event(agent, task, event, payload):
    if not agent._running or event not in EVENT_NAMES:
        return None
    public = {key: value for key, value in payload.items() if key in PUBLIC_FIELDS}
    name = EVENT_NAMES[event]
    if event in {'run_finished', 'run_failed'}:
        name = 'run.completed' if task.status == 'completed' else 'run.failed' if task.status == 'failed' else 'run.stopped'
        agent.last_result = agent.build_result()
        public['result'] = agent.last_result.to_dict()
    if event == 'tool_executed' and payload.get('tool_status') in {'error', 'rejected', 'unknown', 'partial_success'}:
        name = 'tool.rejected' if payload['tool_status'] == 'rejected' else 'tool.failed'
    correlation = payload.get('call_id') or payload.get('checkpoint_id') or payload.get('compaction_id')
    if event.startswith('model_'):
        correlation = f'{task.run_id}:model:{task.attempts}'
    agent._event_sequence += 1
    return AgentEvent(task.run_id, agent._event_sequence, name, public, str(correlation or task.run_id))
