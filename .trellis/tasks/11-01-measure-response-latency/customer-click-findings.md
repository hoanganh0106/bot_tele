# Customer navigation findings

Measured real user interactions on the deployed instrumented bot on 2026-11-01,
11:23–11:24 UTC (18:23–18:24 Asia/Bangkok). Full safe timing records are in
server-user-clicks.jsonl. All six measured callbacks returned successfully.

| Handler | Total ms | Edit ms | Acknowledge ms | Residual ms |
| --- | ---: | ---: | ---: | ---: |
| Category | 681.094 | 349.854 | 326.569 | 4.671 |
| Product | 688.583 | 358.634 | 326.631 | 3.318 |
| Category | 677.963 | 348.765 | 325.512 | 3.686 |
| Back to menu | 731.342 | 400.751 | 326.874 | 3.717 |
| Category | 729.972 | 397.898 | 328.627 | 3.447 |
| Product | 677.001 | 349.532 | 324.040 | 3.429 |

Residual is total minus the two non-overlapping, sequential Telegram waits
within each exact same handler span. It includes local work, other short waits
and diagnostic overhead; it is not a pure CPU benchmark. The two Telegram
waits account for over 99% of each sampled handler duration.

Menu building took 3.607 ms inside the back-menu callback. Other catalog reads
in the journal window served fresh cache, median 0.142 ms (104 reads, including
unrelated background/admin operations). No customer reload-menu callback was
captured, so forced-refresh timings must not be presented as a measured reload
click. No ERROR lines in the observed window; service active.

Conclusion: these customer navigation callbacks are dominated by waiting for
Telegram, which explains why a language migration provides little perceived
latency improvement on the sampled actions. This is stronger evidence than
the earlier getMe probes because it attributes child waits within actual
callback spans.

Potential next change: for successful navigation, overlap the callback
acknowledgement with the message edit, preserving single acknowledgement and
existing alert semantics on validation/error paths. This could reduce total
handler completion/spinner duration toward the slower of the two requests
(approximately 350–400 ms in this sample). It does not imply halving message
render latency: edits already execute first and finish at about 350–400 ms.
Moving acknowledgement before the edit sequentially would shorten the spinner
but delay the message update; do not mistake that for a throughput improvement.
Concurrent-request performance must be measured after a separately reviewed
behavior change; the estimate is not a demonstrated speedup.
