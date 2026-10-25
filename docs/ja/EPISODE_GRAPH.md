# Episode graph

[English](../EPISODE_GRAPH.md)

`trailgate export graph ARCHIVE --out private-graph.json` は、非公開の `trailgate.episode_graph.v1` graph を書き出します。episode、trace、generation decision、tool action、渡された値、observation の parent link、score の対象について、ソース ID と参照を保持します。capture された prompt と tool 結果も含むため、非公開データとして扱ってください。

episode は**境界が不明なグループ**です。一貫した空でない session ID を持つ observation は、trace ID をまたいで project と session ごとにまとめます。それ以外は project と trace ごとにまとめます。このグループ化は、そこで task が始まったり終わったりしたことを意味しません。`history_relation` と `context_compaction` は `unknown` のままです。Trailgate は capture された各 prefix をそのまま保持し、隣接する observation から欠落を補いません。重複するテキストも除去しません。

`raw_capture_order` は sidecar 内の行順を記録します。decision と action の link は、記録された関係だけを表します。つまり generation の capture ID、assistant の tool-call ID、渡した結果の記録、参照先の Langfuse TOOL observation です。Langfuse の parent link には独自の関係ラベルを付け、モデルに見えた履歴へ格上げしません。一つの decision から出た複数の call は別々の action のままです。繰り返された call は、アプリがより強い根拠を出さない限り `retry_relation: unknown` です。結果の欠落、中断された decision、解決できない score の対象、親 decision のない TOOL observation も graph に残します。

score は、申告された対象に後から得られた根拠として紐づけます。過去の decision の `visible_prefix` には入れず、回答の正しさも証明しません。capture された元の provider response は、不透明な payload として対応する decision に保持します。Responses payload を Chat message へ変換しません。
