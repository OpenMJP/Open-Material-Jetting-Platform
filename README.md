# Open Material Jetting Platform (PAJM)

Plataforma aberta de controle e posicionamento de um cabeçote industrial de jateamento de material (*material jetting*, MJT), para pesquisa em manufatura aditiva. O projeto cobre a cadeia inteira, do arquivo à gota: firmware do controlador, protocolo de comunicação e um programa de controle no PC, que inclui um fatiador.

*An open platform for driving an industrial material jetting printhead (Xaar XJ128) for additive manufacturing research: ESP32-S3 firmware, a documented PC ↔ firmware protocol, and a desktop control program with a slicer. Documentation is in Portuguese.*

Trabalho de Conclusão de Curso de Engenharia Mecatrônica na **UTFPR, Campus Curitiba**.

<!--
- **Autores:** Arthur Benatto (firmware e eletrônica) e Gabriela Kemper (programa de controle e mecânica)
- **Orientação:** Prof. Dr. Neri Volpato
!-->


<!-- FOTO: coloque aqui uma foto da máquina ou do cabeçote jateando, por exemplo:
![Máquina PAJM](docs/img/maquina.jpg)
-->

---

## O que a plataforma faz

- Jateia imagens (PNG, PDF) e padrões com meio-tom, com posição e escala definidas em milímetros.
- Fatia modelos STL em camadas e as imprime sobrepostas, com opacidade e contorno por camada, para demonstrar a construção 3D em papel.
- Importa pacotes de camadas `.pajm` gerados por outro programa de planejamento de processo (formato aberto, em [`IMPORTACAO.md`](IMPORTACAO.md)).
- Calibra a máquina com padrões impressos: verificação de bicos com resolução ajustável, ângulo do cabeçote, avanço entre faixas, grade dimensional.
- Permite controlar os parâmetros de processo, como a velocidade do carro e a resolução de jateamento.

## Hardware

| Parte | Escolha |
|---|---|
| Cabeçote | Xaar XJ128/360: 128 bicos, gota de 40 pL, 360 × 360 dpi montado a 31°, faixa de 9 mm |
| Controlador | ESP32-S3 (YD-ESP32-S3 N16R8), com ESP-IDF 5.5.5: um núcleo cuida dos motores e da comunicação, o outro só do cabeçote |
| Eletrônica | Placa própria no KiCad, com buffers 74HCT244 para os sinais do cabeçote |
| Comunicação | USB serial pelo CH343P, a 2 Mbaud |
| Mecânica | Cartesiana (base de Prusa i3), NEMA 17 com drivers A4988, mesa de 200 × 200 mm |
| Segurança | Watchdog externo que corta a alta tensão do cabeçote se o firmware parar |

## Estado do projeto

O sistema está montado, integrado e testado com o cabeçote, imprimindo imagens, padrões de calibração e peças fatiadas em camadas sobrepostas. O disparo é sincronizado com a posição do carro pela contagem de passos do motor, sem encoder. Medido com analisador lógico, o atraso entre o passo do motor e o disparo é de cerca de 4,2 µs, com variação de 190 ns.

Ficaram para depois do TCC a impressão nos dois sentidos de movimento, o uso de mais de um cabeçote e a construção de peças por camadas com cura entre elas.

## Estrutura do repositório

| Pasta ou arquivo | Conteúdo |
|---|---|
| [`PAJM 1.0/`](PAJM%201.0/) | Firmware do ESP32-S3: movimento dos eixos, disparo do cabeçote sincronizado com a posição, protocolo |
| [`CONTROLE 1.0/`](CONTROLE%201.0/) | Programa de controle (Python e PySide6): painel, fatiador, impressão de imagem, importação de pacotes, calibração |
| [`PROTOCOL.md`](PROTOCOL.md) | Protocolo PC ↔ firmware (v1.4): quadros, sessão, swaths, códigos de erro |
| [`IMPORTACAO.md`](IMPORTACAO.md) | Formato `.pajm` para importar camadas de outro programa de planejamento de processo |
| [`CONTEXTO-PAJM.md`](CONTEXTO-PAJM.md) | Cabeçote, hardware, geometria, decisões de projeto e estado atual |

A divisão de responsabilidades é estrita: **o PC não faz timing, o firmware não faz geometria.** O PC fatia, rasteriza e compensa a inclinação do cabeçote. O firmware recebe colunas prontas e dispara cada uma na posição certa do eixo X.

## Como usar

### Firmware

Requer o [ESP-IDF 5.5.5](https://docs.espressif.com/projects/esp-idf/). Dentro de `PAJM 1.0`, com o ambiente ativado:

```
idf.py build
idf.py -p COMx flash
```

A gravação e os logs usam o conector **USB** (nativo) da placa. O protocolo com o PC usa o conector **COM** (CH343P). Detalhes em [`PAJM 1.0/README.md`](PAJM%201.0/README.md).

### Programa de controle

Requer Python 3 no Windows. Num ambiente virtual fora de pastas sincronizadas (OneDrive e semelhantes):

```
python -m venv %USERPROFILE%\.venvs\pajm
%USERPROFILE%\.venvs\pajm\Scripts\python.exe -m pip install -r "CONTROLE 1.0\requirements.txt"
%USERPROFILE%\.venvs\pajm\Scripts\python.exe "CONTROLE 1.0\PAJM_Controle.py"
```

Sem a máquina, escolha a porta **Simulador** na barra superior: o firmware simulado responde como a máquina real, inclusive recusando o que ela recusaria.

Para gerar o executável, rode `CONTROLE 1.0\build.bat`. Ele roda todos os testes antes e só atualiza o `.exe` se passarem.

### Testes

Cada parte do programa de controle tem um conjunto de testes, que rodam sem a máquina e sem monitor:

```
cd "CONTROLE 1.0"
python testes/teste_protocolo.py
python testes/teste_interface.py
```

## Licenças

- **Código** (firmware e programa de controle): [GNU GPL v3.0](LICENSE). Qualquer pessoa pode usar, estudar, modificar e distribuir o código, inclusive comercialmente. Quem distribuir o software ou uma máquina com ele, modificado ou não, tem de disponibilizar o código-fonte completo sob a mesma licença.
- **Documentação** (os arquivos `.md` e a monografia): [Creative Commons BY-NC-SA 4.0](LICENSE-DOCS). Pode ser compartilhada e adaptada com atribuição, para fins não comerciais, sob a mesma licença.

Os documentos do fabricante do cabeçote (manual e folha de dados da Xaar) não fazem parte deste repositório.
