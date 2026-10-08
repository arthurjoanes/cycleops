# Resultados

Cada execução registrada no repositório, com o commit do código usado. Comandos rodam da raiz; `uv` pode ser `python -m uv`.

| Resultado | Arquivo | Comando | Commit | Data (UTC) |
|---|---|---|---|---|
| Coleta única dos 5 feeds | `fixtures/recorded/inspect-20261007T032439Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend inspect-feed --deadline-seconds 30` | 0da0d2c (rodou antes do commit, com o mesmo cliente) | 2026-10-07 03:24 |
| Amostra de 10 min | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `CYCLEOPS_CLIENT_IDENTIFIER=arthurjoanes-cycleops uv run --project apps/backend record-feed --minutes 10 --deadline-seconds 30 --out fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 0da0d2c (idem) | 2026-10-07 03:24 a 03:34 |
| Validação do feed | `docs/results/feed-validation.md` | `uv run --project apps/backend inspect-feed --recording fixtures/recorded/inspect-20261007T032439Z.ndjson.gz fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | 98717c1 | 2026-10-07 |
| Testes Python, ruff e mypy: 130 testes passando, sem rede | | `uv run --project apps/backend pytest apps/backend`, `ruff check apps/backend`, `mypy --config-file apps/backend/pyproject.toml apps/backend/src apps/backend/tests` | 98717c1 | 2026-10-07 |
| Artefato sintético da demo: 242 eventos, 85 KB em gzip, fechamento à 01:00 e incidentes resolvidos | `apps/web/public/replay/synthetic/` | `uv run --project apps/backend generate-artifacts` | c5845b1 | 2026-10-08 |
| Aceite da amostra gravada de 10 min: não passa (40 incidentes confirmados, nenhum resolvido), saída 3 | `fixtures/recorded/sample-20261007T032452Z.ndjson.gz` | `uv run --project apps/backend compile-replay --recording fixtures/recorded/sample-20261007T032452Z.ndjson.gz --out data/replay-check` | c5845b1 | 2026-10-08 |
| Testes Python, ruff e mypy da fase 1: 215 testes passando, sem rede; `generate-artifacts --check` sem diferença | | `uv run --project apps/backend pytest apps/backend`, `generate-artifacts --check` | c5845b1 | 2026-10-08 |

## Pendente

A coleta de dia, de outras redes e a gravação de 60 a 90 min estão em "Coleta pendente", em `feed-validation.md`.
