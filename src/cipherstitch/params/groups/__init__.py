"""Every Research-mode parameter, by group. The defaults are the engine's constants: Standard
uses them and nothing else. A new method or cipher adds its own group in these modules and to
:data:`GROUPS`, and the editor, the profiles and the settings pick it up."""

from cipherstitch.params.groups.audio import AUDIO_LSB, ECHO, PHASE
from cipherstitch.params.groups.coding import STC
from cipherstitch.params.groups.images import DCT, IMAGE_LSB, PRECOVER
from cipherstitch.params.groups.security import CRYPTO, FRAME
from cipherstitch.params.groups.text import OBJECT, WHITESPACE, ZERO_WIDTH
from cipherstitch.params.model import Group

GROUPS: tuple[Group, ...] = (
    CRYPTO,
    FRAME,
    STC,
    IMAGE_LSB,
    AUDIO_LSB,
    DCT,
    PRECOVER,
    PHASE,
    ECHO,
    OBJECT,
    WHITESPACE,
    ZERO_WIDTH,
)
GROUP_IDS = frozenset(group.id for group in GROUPS)  # an extension's group takes none of them
