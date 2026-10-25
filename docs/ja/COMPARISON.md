# ソースの export に対して Trailgate が加えるもの

[English](../COMPARISON.md)

この比較は、同じ架空の二 decision の検索 fixture を使います。実際のモデルの回答品質については主張しません。ローカルの経路横断テストでは、同じ記録から保存済み API page、mock の読み取り専用 pull、複製した enriched blob run を構築し、正規化された意味上の field、ソース hash、capture 不在時の挙動を確認します。self-hosted の live API については、架空の telemetry で別途検証しました。これらの検査は、任意の Langfuse 環境やすべての blob 形式での同等性を証明するものではありません。

| 手順 | この fixture で得られるもの | 学習上の制約 |
|---|---|---|
| Langfuse API または enriched blob export のみ | ソース observation、score、metadata、export の出典情報。 | observation だけでは、モデルに渡された正確な message prefix や tool 結果を確定できません。score は対象に紐づく根拠であり、品質の自動承認ではありません。 |
| Trailgate で取り込み、`export graph` を実行 | capture されている場合に、ソースと紐づく decision、tool action、渡した値を得ます。境界の不明点や欠落した link も明示します。 | graph は内容を含む診断用データです。正しさや利用許可を認定しません。 |
| Trailgate の `evaluate dataset` と `export dataset` | decision ごとの gate 理由、条件を満たす Chat SFT view、診断用 episode、hash で紐づく manifest。 | 対応する Chat 形式だけが SFT に入ります。権利、プライバシー、品質は利用者の申告に依存します。学習や upload は行いません。 |
| [smithtune](https://github.com/langchain-ai/smithtune)（ローカルソースの確認時点: `e5327ec6fc780732085abdf3bbbfa4489911e008`） | LangSmith に基づく軌跡の準備から provider での学習、評価、任意の deployment までを扱い、独自の権利確認を求めます。 | 入力元と出力先の契約が異なります。この Langfuse fixture について行単位の直接比較は実行していません。 |

比較対象は機能の範囲と根拠です。性能 benchmark でも、一方が他方を置き換えるという主張でもありません。Trailgate v1 の対象は、ローカルで監査可能な export までです。
