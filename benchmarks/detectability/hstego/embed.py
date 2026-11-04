"""HStego's embedding at a payload above its own limit (the HStego container).

    /opt/hstego/bin/python /opt/hstego/embed.py COVER PAYLOAD OUTPUT PASSWORD

``hstego.py embed`` refuses a message above 5% of HStego's capacity (``MAX_PAYLOAD``), and
the study's references carry CipherStitch's payload, which is often more. This runs the same
embedding as ``hstego.py embed`` (S-UNIWARD for a PNG, J-UNIWARD for a JPEG) with the limit
lifted, after printing the capacity the released tool allows, for the manifest.

The limit is lifted by scaling the capacities HStego splits the message by, so each colour
channel gets the share the released tool gives it. Raising ``MAX_PAYLOAD`` instead would
split by capacities fine enough that the embedding's own changes move them, and the
message could no longer be extracted.
"""

import sys

import hstegolib
import numpy as np
from PIL import Image

LIFT = 1000  # times the released capacity: far above any payload of the study


def released_capacity(cover: str) -> int:
    """The capacity ``hstego.py embed`` checks the message against, in bytes."""
    if cover.endswith(".png"):
        return int(hstegolib.spatial_capacity(np.asarray(Image.open(cover))))

    return int(hstegolib.jpg_capacity(hstegolib.jpeg_load(cover)))


def main() -> None:
    cover, payload, output, password = sys.argv[1:]
    print(f"released capacity: {released_capacity(cover)} bytes")
    released = hstegolib.effective_payload_capacities
    hstegolib.effective_payload_capacities = lambda capacities: [
        capacity * LIFT for capacity in released(capacities)
    ]
    method = hstegolib.S_UNIWARD() if cover.endswith(".png") else hstegolib.J_UNIWARD()
    method.embed(cover, payload, password, output)


if __name__ == "__main__":
    main()
