# Audio

Every file here is synthesised by `tools/build_audio.py` from sine, triangle, square and noise generators with
fixed seeds. There are no recordings, samples or third-party sounds, and a rebuild is byte-identical:

    uv run python tools/build_audio.py

| File | Played when |
| --- | --- |
| `door_chime.wav` | a customer walks in |
| `register.wav` | the cashier takes an order |
| `ticket.wav` | a ticket goes to the kitchen; a wasted box is binned |
| `oven_whoosh.wav` | a pizza goes into the oven |
| `order_bell.wav` | the chef sets a box on the counter |
| `click.wav` | a customer picks up their box |
| `fail.wav` | an order fails |
| `restock.wav` | a delivery arrives |
| `sting.wav` | the chef catches the stock sheet lying |
| `oven_crackle.wav` | loops while anything bakes |
| `room_tone.wav` | loops quietly under everything |
| `music.wav` | a 16-bar lo-fi loop at 88 BPM |

The viewer plays them as 22.05 kHz mono and pans effects by where they happen on stage. Press M to mute, or pass
`--no-audio`. Covered by the project's asset licence like the rest of the original artwork.
