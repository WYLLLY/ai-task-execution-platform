"""Pinned Responses handler reusing M3 transactions and M4 read/report steps."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.agent_protocol import encode
from task_platform.command_schemas import TaskInput
from task_platform.execution_models import ExternalCall
from task_platform.provider_admin import definition
from task_platform.responses import ProviderProfile, build_request, estimate_input, price
from task_platform.task_models import Step, Task
from task_platform.tasks import Factory
from task_platform.workflow import AnalysisExecutor


class ResponsesExecutor(AnalysisExecutor):
    def __init__(
        self, factory: Factory, control: Factory, epoch: UUID, profile: ProviderProfile
    ) -> None:
        super().__init__(factory, control, epoch)
        self.profile = profile

    def handler_available(self, task: Task, step: Step | None = None) -> bool:
        return (
            task.workflow_name == "analysis"
            and task.workflow_version == "2"
            and task.versions == definition(self.profile)
        )

    def call_kind(self, step: Step) -> str:
        return "demo-v1" if step.path == "analysis/save-report" else "openai-v1"

    def reservation(self, step: Step, payload: bytes) -> tuple[int, int, str, str]:
        if self.call_kind(step) == "demo-v1":
            return super().reservation(step, payload)
        count = estimate_input(payload)
        return (
            price(self.profile, count, self.profile.output_tokens, 0),
            count + self.profile.output_tokens,
            self.profile.tariff,
            self.profile.tokenizer,
        )

    def build_model_context(
        self, task: Task, history: list[dict[str, object]], previous: list[Step]
    ) -> bytes:
        protocol: list[dict[str, object]] = []
        for prior in previous:
            if prior.path.endswith("/model"):
                if prior.protocol_items is None:
                    raise ValueError("Missing protocol checkpoint")
                protocol.extend(prior.protocol_items)
            else:
                if prior.result is None or not isinstance(prior.result.get("items"), list):
                    raise ValueError("Missing tool checkpoint")
                items = prior.result["items"]
                if not isinstance(items, list):
                    raise ValueError("Invalid tool checkpoint")
                for item in items:
                    protocol.append(
                        {
                            "type": "function_call_output",
                            "call_id": item["call_id"],
                            "output": encode(item["output"]).decode(),
                        }
                    )
        return build_request(self.profile, TaskInput.model_validate(task.input).texts, protocol)

    async def advance(
        self, session: AsyncSession, task: Task, step: Step, call: ExternalCall, result: object
    ) -> None:
        if step.path == "analysis/save-report":
            await super().advance(session, task, step, call, result)
            return
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("protocol_items"), list)
            or not await self.charge_checkpoint(session, task, result["protocol_items"])
        ):
            step.state = task.state = "failed"
            task.reason = "invalid_model_output"
            return
        step.protocol_items = result["protocol_items"]
        await super().advance(session, task, step, call, result.get("canonical"))
