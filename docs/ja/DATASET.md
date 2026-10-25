# データセットの適格性と export

[English](../DATASET.md)

`trailgate export sft` は v0.1 との互換性のために残した**候補診断用の変換結果**です。capture の構造、Chat 形式、利用目的についての自己申告 manifest を確認します。回答品質、プライバシー、データ分割は確認しません。出力行には `eligibility: candidate_only` が付きます。

内容を書き出さずに gate の件数と理由を確認するには `trailgate evaluate dataset` を使います。条件を満たす学習用 view は `trailgate export dataset` で作成します。

```sh
uv run --locked trailgate export dataset private-archive \
  --rights-file examples/synthetic/rights.json \
  --policy-file examples/synthetic/policy.json \
  --use internal_training --out private-dataset
```

付属の例示ファイルは、**変更していない架空の fixture にだけ**適用されます。実際の export には、その `snapshot_id` に紐づけて新たに確認した申告が必要です。rights manifest は task input、model output、tool result それぞれについて、指定された用途の許可を別々に与えます。policy は手動承認した generation ID を指定します。score だけで decision は承認されません。プライバシー確認では機微情報がないことと `redaction: none` を明示する必要があります。Trailgate は秘密情報を検出せず、自動墨消しも実装していません。機微情報が含まれる可能性がある場合、Trailgate 外で対処し、新たに確認した snapshot を作るまで学習用 export は停止します。これらの宣言は利用者の申告を検査するものであり、法的権利やプライバシーの認証ではありません。

policy は trace ID を task ID と `train`/`validation`/`test` の split にも対応づけます。一つの task が複数の split にまたがることはできません。Chat SFT では、同じ project snapshot 内のすべての observation に、trace ごとに一貫した空でない session ID が必要です。該当 project 内のどれか一つの trace に session metadata の欠落や矛盾があれば、その trace が同じ session の仲間かもしれないため、project 内のすべての decision が session gate を通りません。同じ session 内で観測された仲間の trace も、task と split が一致する必要があります。project をまたいで同じ trace ID が使われた場合は、policy から区別できないため停止します。split がない、`train` 以外、手動承認がない、構造上不完全、または非対応の provider 形式の generation は、gate 理由を分けて非公開の一般 episode view に残り、`chat_sft.jsonl` には入りません。provider 固有の response は episode view と元の archive に不透明なまま残します。`target` は最後の assistant message だけを loss 対象として示します。token 化とモデル固有の loss mask は Trailgate の対象外です。

出力ディレクトリには `episodes.jsonl`、`chat_sft.jsonl`、`manifest.json` が入ります。`episodes.jsonl` は診断用 view です。観測された一つの session に複数の task や split の trace が含まれ得るため、その行を学習可能とは表示しません。session gate は archive 内で見える矛盾を検出しますが、手動で capture した prompt text に外部のデータが混ざっていないことまでは証明できません。generation の選択時には人間がこの境界を確認する必要があります。検証済み Chat 行には、取得できた request parameters も保持します。manifest には各 view の状態、選択件数、除外理由、ソースと出力の hash、変換 schema の version、権利と policy の hash、trace と task の split 根拠を記録します。同じ archive と申告ファイルの bytes で export を繰り返すと、出力ファイルは byte 単位で一致します。出力は既定で非公開です。共有や学習の前に内容を確認してください。
