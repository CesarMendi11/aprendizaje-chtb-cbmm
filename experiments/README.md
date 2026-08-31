# Evaluación experimental de Chat-CBMM

Este directorio contiene el protocolo, las referencias humanas y los bancos de consultas de la evaluación científica. El producto evaluado vive en `src/erp_assistant`; los instrumentos de medición viven en `scripts/experiments`.

Reglas principales:

- No utilizar la matriz de regresión 26/26 como Gold Standard científico.
- Separar preguntas de desarrollo (`query_bank/development.json`) de evaluación final (`query_bank/evaluation.json`).
- Congelar sistema, conocimiento ACTIVE, baseline, modelos, prompts, parámetros, banco de consultas y Gold Standard antes de la comparación final.
- Después del freeze, cualquier corrección que pueda afectar resultados exige repetir la evaluación afectada completa.
- Los resultados generados se conservan localmente bajo `experiments/results/` y no se versionan por defecto para evitar publicar evidencia potencialmente sensible.
