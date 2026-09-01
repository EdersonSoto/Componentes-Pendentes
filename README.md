# Componentes-Pendentes

Aplicativo desktop para gestão de assistência técnica: controla equipamentos em reparo, orçamentos enviados a clientes e o acompanhamento de peças/componentes pendentes até a liberação efetiva do serviço.

## Sobre o projeto

O sistema centraliza o fluxo de uma oficina/assistência técnica, desde a entrada do equipamento até a finalização do serviço, com controle de acesso por usuário e histórico de todas as alterações.

## Funcionalidades

- Cadastro, edição e finalização de equipamentos em atendimento, com indicador automático de status (pendente, aprovado, atrasado etc.) calculado a partir das datas.
- Gestão de orçamentos: criação, envio, aprovação e reabertura, com extração e interpretação automática de texto de PDFs de orçamento.
- Controle de "componentes pendentes": acompanhamento de peças aguardando chegada/aprovação até a liberação efetiva do equipamento.
- Login com controle de acesso e senha de administrador para ações sensíveis (exclusão, reabertura de itens finalizados).
- Histórico completo de ações por pedido/equipamento (quem alterou, quando e o quê).
- Geração de documentos em PDF.
- Backup local do banco de dados.
- Ícone na bandeja do sistema (system tray), permitindo manter o app em segundo plano.
- Interface gráfica nativa construída com Tkinter.

## Tecnologias utilizadas

- **Python 3**
- **Tkinter** — interface gráfica
- **PostgreSQL** (via `psycopg2`) — banco de dados
- **pypdf** — leitura e interpretação de PDFs de orçamento
- **Pillow** — geração de ícones
- **pystray** — ícone na bandeja do sistema
- **Inno Setup** — geração do instalador Windows (`instalador.iss`)

## Como executar

### Pré-requisitos

- Python 3.10+
- Um banco PostgreSQL acessível (local ou remoto)

### Passos

```bash
git clone https://github.com/EdersonSoto/Componentes-Pendentes.git
cd Componentes-Pendentes
pip install -r requirements.txt
python app.py
```

No Windows, também é possível iniciar pelo atalho `Iniciar Painel.bat`.

Na primeira execução, configure a conexão com o banco de dados conforme solicitado pela aplicação.

### Gerando o instalador (Windows)

O projeto inclui um script do Inno Setup (`instalador.iss`) para empacotar a aplicação em um instalador `.exe`.

## Estrutura do projeto

```
Componentes-Pendentes/
├── app.py              # Aplicação principal (interface, regras de negócio, acesso ao banco)
├── assets/              # Ícones e recursos visuais
├── instalador.iss       # Script do instalador (Inno Setup)
├── Iniciar Painel.bat    # Atalho para iniciar o app no Windows
└── requirements.txt      # Dependências Python
```

## Autor

Desenvolvido por [Ederson Soto](https://github.com/EdersonSoto).
