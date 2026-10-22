-- 0002 · salvaguarda de la GPU: motivo por el que una extracción sigue en cola (p. ej. la GPU de LM Studio está
-- ocupada por otro programa); NULL si no espera.

ALTER TABLE extractions ADD COLUMN waiting TEXT;
