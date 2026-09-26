"""Freeze execution eligibility from the governing Gold task constraints.

This selection does not build an agent prompt or grant execution. Stage 10
builds the actual context from admitted knowledge and an accepted plan, and
rechecks eligibility at the authorized invocation boundary.
"""

from synapse.agents.codec import digest
from synapse.agents.configuration import registry_from_configuration
from synapse.agents.contracts import LocalInformationPolicy
from synapse.agents.outputs import PATCH_CANDIDATE_OUTPUT_V1
from synapse.agents.registry import AgentRegistry, AgentRequirements
from .stage10.task_contract import GoverningTaskContract
from .stage10.intent import EffectKind, EffectDisposition


def select_coding_agent(declaration, *, context=None, selected=None):
    registry = registry_from_configuration(declaration['agents'], context=context)
    task = GoverningTaskContract.from_dict(declaration['task_contract'])
    if ('repository.edit' not in task.required_capabilities
            or any(e.kind is not EffectKind.PATH_MODIFIED for e in task.effects
                   if e.disposition is EffectDisposition.EXPECTED)):
        raise ValueError('this Gold verification profile accepts governed coding effects only')
    eligible = tuple(a for a in registry.adapters if (
        a.profile.provider_name == declaration['config']['provider']
        and a.profile.model_name == declaration['config']['model']
        and a.profile.resource_limits.timeout_seconds <= declaration['config']['budgets']['maximum_wall_clock_seconds']))
    if not eligible:
        raise ValueError('NO_ELIGIBLE_AGENT: the governed run identity or budget excludes every profile')
    names = {a.profile.profile_id for a in eligible}
    registry = AgentRegistry(eligible, admissions=tuple(registry.admission(a) for a in eligible),
        preferred_profiles=tuple(p for p in declaration['agents']['preferred_profiles'] if p in names),
        retained_evidence=registry.retained_evidence)
    adapter = registry.resolve(AgentRequirements(required_capabilities=('repository.edit',),
        required_output_profile=PATCH_CANDIDATE_OUTPUT_V1, required_effect_classes=('PATH_MODIFIED',),
        allowed_effects=('PATH_MODIFIED',), media_types=(), information_policy=LocalInformationPolicy.LOCAL_ONLY,
        allowed_networks=('LOCAL_BROKER', 'NONE')))
    binding = {'profile_id': adapter.profile.profile_id, 'profile_sha256': digest(adapter.profile),
               'selection_policy': 'operator-preference-profile-id/v1'}
    if selected is not None and selected != binding:
        raise ValueError('agent selection differs from the frozen run')
    return binding, AgentRegistry((adapter,), admissions=(registry.admission(adapter),), retained_evidence=registry.retained_evidence)
