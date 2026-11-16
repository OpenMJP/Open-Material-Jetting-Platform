# Open Material Jetting Platform / Plataforma Aberta de Jateamento de Material
 
Sistema aberto e de baixo custo para controlar um cabeçote piezoelétrico Xaar XJ128/360, voltado à pesquisa em manufatura aditiva por jateamento de material (MJT).
 
O projeto foi desenvolvido como Trabalho de Conclusão de Curso em Engenharia Mecatrônica na UTFPR, Campus Curitiba, no NUFER (Núcleo de Manufatura Aditiva e Ferramental).
 
<!-- FOTO: coloque aqui uma foto da máquina ou do cabeçote jateando, por exemplo:
![Máquina PAJM](docs/img/maquina.jpg)
-->
 
Nesta versão, o sistema jateia padrões 2D sobre um substrato, usando tinta à base de solvente.
 
## Detalhes do projeto
 
- **Cabeçote:** Xaar XJ128/360, com 128 bicos, montado a 31° para chegar a 360 × 360 dpi em uma faixa de 9 mm.
- **Mecânica:** estrutura cartesiana de um clone de Prusa i3, com três motores NEMA 17 e mesa de 200 mm.
- **Eletrônica:** placa própria feita no KiCad, com um ESP32-S3, buffers 74HCT244 para os sinais do cabeçote e um watchdog em hardware que desliga a alta tensão do cabeçote se o firmware travar.
- **Firmware:** ESP-IDF. Um núcleo cuida dos motores e da comunicação, e o outro só do cabeçote. O disparo é sincronizado com a posição do carro pela contagem de passos do motor.
- **Software:** aplicação em Python para Windows. Converte a imagem nos dados de disparo, compensa a inclinação do cabeçote e envia tudo ao ESP32 pela USB.
O computador cuida da geometria e o ESP32 cuida do tempo. O ESP32 recebe as colunas de bits já prontas e dispara cada uma na posição indicada.
 
## Estrutura do repositório
 
<!-- Ajuste conforme as pastas reais, ou troque por links para os outros repositórios da organização. -->
 
- `firmware/`: código do ESP32-S3
- `software/`: aplicação desktop
- `hardware/`: projeto da placa no KiCad
- `mecanica/`: modelos das peças impressas
- `docs/`: protocolo de comunicação e documentação técnica
## Estado do projeto
 
O sistema está montado, integrado e testado com o cabeçote. A temporização do disparo foi medida com analisador lógico: o atraso entre o passo do motor e o disparo é de cerca de 4,2 µs, com variação de 190 ns.
 
Ficaram para depois do TCC a impressão nos dois sentidos de movimento, o uso de mais de um cabeçote e a construção de peças por camadas.
 
## Autores
 
Arthur Celestino (programa embarcado e eletrônica) e Gabriela Kemper (programa de controle e mecânica), com orientação do Prof. Dr. Neri Volpato.
 
## Licença
 
Código sob a licença MIT. Documentação e monografia sob a licença CC BY-NC-SA 4.0.
