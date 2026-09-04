# Evaluación experimental de Chat-CBMM

Protocolo activo candidato a freeze: `protocol/protocol_v2.json`. Este protocolo reemplaza metodológicamente al borrador `protocol/protocol_v1.json`, que se conserva para trazabilidad histórica.

Diseño formal: RQ1 adquisición estructural contra referencia humana independiente; RQ2 calidad semántica PRE/POST-HITL y esfuerzo humano; RQ3 comparación pareada A/B/C con ablación secundaria graph ON/OFF. La selección de modelos pertenece a DEVELOPMENT y se reporta como justificación de diseño. SUS se mantiene como evaluación exploratoria con una muestra objetivo de 15 usuarios ERP.

**Estado:** datos formales de RQ1/RQ2/RQ3 todavía NO iniciados.

Este directorio contiene el protocolo, las referencias humanas y los bancos de consultas de la evaluación científica. El producto evaluado vive en `src/erp_assistant`; los instrumentos de medición viven en `scripts/experiments`.

Reglas principales:

- No utilizar la matriz de regresión 26/26 como Gold Standard científico.
- Separar preguntas de desarrollo (`query_bank/development.json`) de evaluación final (`query_bank/evaluation.json`).
- Congelar sistema, conocimiento ACTIVE, baseline, modelos, prompts, parámetros, banco de consultas y Gold Standard antes de la comparación final.
- Después del freeze, cualquier corrección que pueda afectar resultados exige repetir la evaluación afectada completa.
- Los resultados generados se conservan localmente bajo `experiments/results/` y no se versionan por defecto para evitar publicar evidencia potencialmente sensible.
