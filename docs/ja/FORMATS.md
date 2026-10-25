# 軌跡形式と相互運用性

[English](../FORMATS.md)

Trailgate v1.0.0 は、version と出典を持つ**非公開 archive**を正本とします。episode graph と dataset file はそこから作る view です。agent telemetry、忠実な軌跡の交換、SFT の例、RL の経験をすべて標準化する単一形式はありません。archive を学習用形式に置き換えると、Langfuse の元の bytes や、episode 境界・モデルに見えた context の不確実性が失われます。

| 形式 | 役割 | Trailgate v1 との関係 |
|---|---|---|
| [Agent Trajectory Interchange Format (ATIF)](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md) | Harbor が使用する JSON の軌跡交換仕様。2026-10-25 の確認時点で RFC は ATIF-v1.8 を説明しています。 | 将来の任意の完全軌跡 export に最も近い候補です。ただし完全で順序の明らかな対話履歴を記述するため、Langfuse の observation だけでは条件を満たせません。Trailgate は現在 ATIF を出力しません。 |
| [OpenTelemetry GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md) と [OpenInference](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md) | agent、model、retrieval、tool span を計装・解釈するための語彙。 | 将来の取り込み adapter の参考になります。どちらも確認済みの学習データセットそのものではなく、モデルに見えた内容の正確さも証明しません。Trailgate v1 は Langfuse の記録を読み、OTLP や OpenInference を直接取り込みません。 |
| [TRL の対話データセット](https://huggingface.co/docs/trl/dataset_formats) | 学習器向けの `messages` と `tools` の例。task に応じた prompt/completion や preference の形式もあります。 | gate を通った `chat_sft.jsonl` は `messages`、`tools`、Trailgate の出典情報と gate 情報を持ちます。ただし **TRL にそのまま渡せる保証はありません**。Trailgate は function arguments を JSON 文字列として保持しますが、TRL の例は object です。対象モデルの chat template と最後の assistant message の loss mask も別途検証が必要です。 |
| [RLDS](https://github.com/google-research/rlds/blob/main/README.md) | 環境の episode と step を順序付きで表し、利用可能な場合は action や reward を含みます。 | 下流の RL adapter には、実際の episode 境界と reward の意味が必要です。Trailgate v1 は reward や RL rollout を作りません。 |

## 現行の契約

- 変更できないソース archive と hash を正本とします。外部形式へ合わせるために、欠落した user turn、tool delivery、非公開の推論内容、reward、終了状態を復元したことにしてはいけません。
- gate を通った Trailgate Chat SFT view には `export dataset` を使用します。`export sft` は旧来の候補診断用です。episode 内に選択された decision があっても、`episodes.jsonl` は診断用のままです。
- 下流 adapter でもソース ID と明示した除外条件を保持します。対応しない provider 形式や不足した根拠は、暗黙に修復せず拒否する必要があります。

## v1 以降の相互運用 gate

順序付きの user・agent・tool event と完全な episode 境界をソースから証明できるようになれば、任意の ATIF adapter が有用です。その adapter は ATIF schema version を固定し、Harbor の参照 model で検証し、拡張 field に Trailgate のソース参照を保持し、不完全な episode を明示的に拒否するべきです。TRL adapter は別途、対象モデルの tokenizer と chat template を検証し、意味を保てる場合にだけ tool arguments を変換し、どの assistant token を loss 対象とするか定義する必要があります。どちらの adapter も v1.0.0 の契約には含まれません。
