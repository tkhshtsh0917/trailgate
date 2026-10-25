# モデル呼び出し境界での capture

[English](../CAPTURE.md)

`CaptureWriter` は非公開の JSONL sidecar を書き出します。モデル推論の直前に、実際の messages と利用可能な tool schemas を渡して `begin_decision` を呼び出してください。明示的な `tools=[]` は利用可能な tool がなかったことを意味し、schema が記録されていなければ不明として扱います。返された capture ID は Langfuse generation の metadata に `trailgate_capture_id` として付けます。

条件を満たす Chat SFT を作るには、収集対象 project snapshot 内のすべての observation が、trace ごとに一貫した空でない session ID を持つよう Langfuse を計装してください。一つでもその根拠を欠く trace があると、その trace が選択対象の session に属する可能性を排除できないため、Trailgate は当該 project snapshot 全体の Chat SFT を止めます。必要なら、完全に計装された小さな snapshot を収集してください。

推論後、実際の assistant message と、取得できる場合は provider 固有の元の response を渡して `finish_decision` を呼びます。推論が失敗したら `abort_decision(capture_id, reason_code="timeout")` を呼びます。実行した各 tool call について、host が実際にモデルへ渡した値と Langfuse の TOOL observation ID を `delivered_tool_result` に記録します。渡した値と observation にある backend output は出典が別であり、Trailgate は同一と仮定しません。

capture の書き込みには POSIX の file lock が必要です。その他の CLI コマンドは POSIX 以外の host でも取り込みに使えます。追記には file lock、event 一件あたり 8 MiB の上限、`fsync` を使います。完了した event は JSONL の完全な一行です。複数 process から一つの sidecar に追記できますが、各 decision 内の因果順序は呼び出し側が守る必要があります。追記が中断すると末尾に不完全な行が残る場合があります。`trailgate capture-recover PRIVATE-CAPTURE --out PRIVATE-RECOVERED` は完全な行を新しい非公開ファイルへコピーし、破棄した末尾の hash を報告します。元ファイルは書き換えません。完全な行が壊れている場合、復旧は失敗します。

sidecar は core library から送信されません。通常の report には prompt、provider response、渡した tool 結果の内容を出しません。capture はアプリ側の申告であり、計装が正しい時点で実行されたかを Trailgate は証明できません。
