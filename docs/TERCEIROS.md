# Componentes de terceiros

O Guará ERP é distribuído sob a **GPL-3.0-or-later**. Ele usa os componentes abaixo, todos com
licenças compatíveis com a GPL v3. Os avisos de copyright originais estão preservados nos próprios
arquivos ou nos pacotes instalados.

## Incluídos no repositório (`static/vendor/`)

| Componente | Versão | Licença | Projeto |
|---|---|---|---|
| Bootstrap (CSS e JS) | 5.3.3 | MIT | https://getbootstrap.com |
| Bootstrap Icons (fonte e CSS) | 1.11.3 | MIT | https://icons.getbootstrap.com |
| Chart.js | 4.4.4 | MIT | https://www.chartjs.org |
| QRCode.js | 1.0.0 | MIT | https://github.com/davidshimjs/qrcodejs |

## Dependências Python (instaladas pelo `requirements.txt`)

| Pacote | Licença |
|---|---|
| Django | BSD-3-Clause |
| psycopg 3 | LGPL-3.0 |
| django-filter | BSD-3-Clause |
| whitenoise | MIT |
| gunicorn | MIT |
| waitress | ZPL-2.1 |
| python-dotenv | BSD-3-Clause |
| tzdata | Apache-2.0 |
| defusedxml | PSF-2.0 |
| segno (QR code do 2FA) | BSD-3-Clause |
| ldap3 (login LDAP/AD) | LGPL-3.0 |
| PyJWT (validação do ID token do SSO) | MIT |

## Serviços usados no Docker (imagens, não redistribuídas)

| Imagem | Licença |
|---|---|
| PostgreSQL 16 | PostgreSQL License |
| pgAdmin 4 | PostgreSQL License |
| Caddy 2 (opcional) | Apache-2.0 |
| ClamAV (antivírus dos uploads) | GPL-2.0 |
| Python 3.12 slim | PSF-2.0 (Python) / Debian |

## Identidade visual

O logotipo, o selo e a ilustração do Cerrado (`static/img/` e `docs/identidade/`) foram fornecidos pelo
autor do projeto, que os criou com a ferramenta de geração de imagens Google Gemini. Eles são
distribuídos com o Guará ERP sob a mesma licença. Veja [identidade/IDENTIDADE.md](identidade/IDENTIDADE.md).
