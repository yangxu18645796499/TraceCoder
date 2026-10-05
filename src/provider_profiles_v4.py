"""Approved immutable provider identities; never auto-detect or fall back."""
from dataclasses import dataclass, asdict

APPROVED_ENDPOINTS = frozenset({
    'https://api.deepseek.com/chat/completions',
    'https://api.deepseek.com/models',
    'https://opencode.ai/zen/go/v1/chat/completions',
})

@dataclass(frozen=True)
class ProviderProfile:
    channel: str
    endpoint: str
    model: str
    key_variable: str
    model_family: str = 'DeepSeek-V4.1-Flash'

    def __post_init__(self):
        approved = {
            ('DeepSeek official', 'https://api.deepseek.com/chat/completions', 'deepseek-flash', 'DEEPSEEK_API'),
            ('OpenCode Go', 'https://opencode.ai/zen/go/v1/chat/completions', 'deepseek-v4.1-flash', 'OPENCODE_API'),
        }
        if (self.channel, self.endpoint, self.model, self.key_variable) not in approved:
            raise ValueError('UnapprovedProviderProfile')

    def document(self):
        return asdict(self)

OFFICIAL = ProviderProfile('DeepSeek official', 'https://api.deepseek.com/chat/completions', 'deepseek-flash', 'DEEPSEEK_API')
