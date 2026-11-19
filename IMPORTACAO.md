# PAJM: formato de importação de camadas (`.pajm`)

Versão **1.0** · 03/11/2026 · Status: proposta para revisão com o RP3

Este documento define o arquivo que um programa de planejamento de processo (CAM) gera para o **PAJM Controle** imprimir. O programa CAM decide a geometria de cada camada. O programa de controle da PAJM decide como jatear cada camada: faixas, compensação do cabeçote, purga e envio à máquina.

O arquivo não contém G-code nem nada específico do cabeçote. Ele contém só a sequência de camadas como imagens e os parâmetros que as descrevem.

---

## 1. Visão geral

- Um arquivo `.pajm` é um **ZIP** comum. Compressão `deflate` ou `stored`, sem senha.
- Na raiz do ZIP ficam:

| Caminho | Obrigatório | Conteúdo |
|---|---|---|
| `manifesto.json` | sim | parâmetros e lista de camadas (§2) |
| `camadas/NNNN.png` | sim | uma imagem por camada (§3) |
| `previa.png` | não | miniatura da peça, só para exibição |

- Os nomes das imagens são livres, desde que estejam listados no manifesto. A recomendação é `camadas/0001.png`, `camadas/0002.png`, e assim por diante.
- Arquivos não citados no manifesto são ignorados.

---

## 2. `manifesto.json`

Texto UTF-8, objeto JSON. As unidades são sempre **milímetros**.

```json
{
  "formato": "pajm-camadas",
  "versao": "1.0",
  "gerador": { "nome": "RP3", "versao": "2.3" },
  "criado_em": "2026-11-03T14:20:00-03:00",
  "peca": { "nome": "piramide", "dimensoes_mm": [20.0, 20.0, 10.0] },

  "pixel_mm": { "x": 0.0750, "y": 0.07056 },
  "origem_mm": { "x": 90.0, "y": 90.0 },
  "espessura_camada_mm": 0.05,

  "camadas": [
    { "z_mm": 0.025, "arquivo": "camadas/0001.png", "sha256": "9f2c..." },
    { "z_mm": 0.075, "arquivo": "camadas/0002.png", "sha256": "41aa..." }
  ],

  "processo": {
    "velocidade_mm_s": 50.0,
    "opacidade": 1.0,
    "mover_z": true
  }
}
```

### Campos obrigatórios

| Campo | Tipo | Significado |
|---|---|---|
| `formato` | texto | sempre `"pajm-camadas"` |
| `versao` | texto | versão deste documento usada para gerar o arquivo (§6) |
| `pixel_mm.x`, `pixel_mm.y` | número > 0 | tamanho do pixel nas imagens, em X e em Y |
| `origem_mm.x`, `origem_mm.y` | número | posição na mesa do canto de X e Y menores: o pixel da coluna 0 na **última** linha, §4 |
| `camadas` | lista, ≥ 1 item | camadas em ordem de impressão, de baixo para cima |
| `camadas[].z_mm` | número > 0 | altura do **meio** da camada acima da mesa |
| `camadas[].arquivo` | texto | caminho da imagem dentro do ZIP |

### Campos opcionais

| Campo | Padrão | Significado |
|---|---|---|
| `gerador` | - | nome e versão do programa que gerou, para o registro |
| `criado_em` | - | data ISO 8601 |
| `peca` | - | nome e dimensões, só para exibição |
| `espessura_camada_mm` | diferença entre `z_mm` vizinhos | informativo; o Z de cada camada vale pelo `z_mm` |
| `camadas[].sha256` | - | SHA-256 da imagem, em hexadecimal minúsculo. Se presente, é conferido |
| `processo.velocidade_mm_s` | a do programa | velocidade de varredura sugerida |
| `processo.opacidade` | 1.0 | fração das gotas por camada, de 0 (exclusive) a 1 |
| `processo.mover_z` | true | false jateia todas as camadas na altura da primeira (demonstração em papel) |

Os valores de `processo` são **sugestões**. O operador pode alterá-los no PAJM Controle antes de imprimir, e a velocidade continua limitada pelo que a máquina aceita.

### Regras

1. `z_mm` é estritamente crescente ao longo da lista.
2. Todas as imagens têm **o mesmo tamanho** em pixels. Uma camada sem nada a jatear é uma imagem toda branca, e não uma entrada omitida.
3. Campos desconhecidos são ignorados. Isso permite acrescentar campos numa versão menor sem quebrar leitores antigos.

---

## 3. Imagens das camadas

- Formato **PNG**, em escala de cinza ou monocromático de 1 bit. PNG colorido é aceito e convertido para cinza.
- **Preto é gota.** Um pixel com luminância abaixo de 50% é jateado. Branco, ou pixel transparente, não é.
- Recomendado: PNG de 1 bit. Tem tamanho pequeno e não deixa margem a dúvida no limiar.
- Sem meio-tom dentro do arquivo: se a camada precisar de opacidade parcial, use `processo.opacidade`. O PAJM Controle escolhe as gotas de modo que camadas sobrepostas não repitam os mesmos pontos.

---

## 4. Sistema de coordenadas

São as coordenadas da **mesa**, as mesmas do fatiador do PAJM Controle:

- X é o eixo de varredura do cabeçote, e Y é o eixo da mesa.
- (0, 0) é o canto da área útil da mesa mais perto dos fins de curso de X e Y. A área útil vai até o curso informado no perfil da máquina (§5).
- A imagem é a camada **vista de cima, com o Y para cima**, como a vista de topo de um CAD e como o impresso é visto de frente para a máquina. Aberta num visualizador de imagens comum, ela aparece como a peça.
- **Coluna 0** é o X menor. A **última linha** do arquivo, a de baixo, é o **Y menor**; a primeira, a de cima, é o Y maior.
- Numa imagem de `H` linhas, o pixel da coluna `c` e linha `l` (contada de cima, a partir de 0) é a gota em
  `x = origem_mm.x + c · pixel_mm.x`  e  `y = origem_mm.y + (H − 1 − l) · pixel_mm.y`.
- Z é medido a partir da mesa, para cima.

A peça inteira precisa caber na área útil. A máquina precisa ainda de espaço em X para acelerar antes da peça e frear depois. O PAJM Controle confere isso antes de imprimir e mostra quanto falta.

---

## 5. Resolução e perfil da máquina

A grade de gotas da PAJM não é fixa:

- em X, depende dos micropassos por disparo;
- em Y, depende do ângulo calibrado do cabeçote.

Por isso a resolução é **declarada** no arquivo (`pixel_mm`), e não imposta:

- **Se `pixel_mm` bate com a grade da máquina** (diferença menor que 0,5% em cada eixo), as imagens são usadas como estão, pixel a pixel.
- **Se não bate**, o PAJM Controle reamostra cada camada para a grade da máquina e avisa o operador. A reamostragem é por área: um ponto da grade nova é jateado se mais da metade da sua área cai em pixels pretos. Um arquivo reamostrado imprime certo em escala, mas pode perder detalhes menores que um pixel.

Para gerar já na grade nativa, sem reamostragem, o PAJM Controle exporta o **perfil da máquina** (página Arquivos, botão "Exportar perfil da maquina..."). O perfil é um JSON:

```json
{
  "formato": "pajm-perfil-maquina",
  "versao": "1.0",
  "pixel_mm": { "x": 0.0750, "y": 0.07056 },
  "dpi": { "x": 338.67, "y": 360.0 },
  "area_util_mm": { "x": 200.0, "y": 200.0 },
  "curso_z_mm": 200.0,
  "faixa_mm": 9.031,
  "bicos": 128,
  "velocidade_max_mm_s": 150.0
}
```

O perfil vale **para a calibração do dia em que foi exportado**. Se o ângulo do cabeçote for recalibrado, exporte de novo. Um arquivo antigo continua imprimindo, apenas reamostrado.

`faixa_mm` é a largura de uma passada do cabeçote. Não é obrigatório usá-la, mas peças com altura em Y múltipla da faixa imprimem com menos passadas.

---

## 6. Versões

- `versao` tem a forma `MAIOR.MENOR`.
- Uma versão **menor** nova só acrescenta campos opcionais: leitores de versão anterior abrem o arquivo e ignoram o que não conhecem.
- Uma versão **maior** nova muda o significado de algo. O PAJM Controle recusa uma versão maior que não conhece, com uma mensagem que diz qual versão o arquivo usa.

| Versão | Data | Mudança |
|---|---|---|
| 1.0 | 03/11/2026 | versão inicial |

---

## 7. Validação na importação

O PAJM Controle recusa o arquivo, dizendo o motivo, quando:

- não é um ZIP, ou falta o `manifesto.json`;
- o `formato` não é `pajm-camadas`, ou a versão maior não é conhecida;
- falta um campo obrigatório, ou um número está fora da faixa;
- os `z_mm` não são estritamente crescentes;
- uma imagem citada não existe, não abre, tem tamanho diferente das outras, ou não confere com o `sha256`;
- o arquivo descompactado passaria de 4 GB, ou uma imagem passaria de 20000 × 20000 pixels. Esses limites protegem contra arquivos corrompidos.

E avisa, sem recusar, quando:

- vai reamostrar (§5);
- a peça sai da área útil ou do curso em Z. Nesse caso a impressão fica bloqueada até reposicionar, como no fatiador.

---

## 8. Exemplo de referência

O fatiador do próprio PAJM Controle exporta qualquer peça fatiada neste formato (botão "Exportar camadas como .pajm..."). O arquivo gerado serve de exemplo para quem implementa o lado do programa CAM, e de teste: exportar e importar de volta tem de dar as mesmas camadas.
