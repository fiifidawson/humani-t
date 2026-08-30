
|                               | **Version 1 - Web Application**                | **Version 2 - Local Application**                                                                         |
| ----------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| **UI**                        | Web                                            | Tauri desktop app                                                                                         |
| **Frontend**                  | Shared frontend                                | Same shared frontend                                                                                      |
| **API**                       | Remote API                                     | Local API                                                                                                 |
| **Backend**                   | Server-based                                   | User's machine                                                                                            |
| **Database**                  | PostgreSQL                                     | SQLite                                                                                                    |
| **Files**                     | Remote/object storage                          | Local filesystem                                                                                          |
| **LLM / Inference**           | Remote LLM/API                                 | Local LLM **or** remote LLM                                                                               |
| **Network**                   | Required                                       | Optional                                                                                                  |
| **Privacy model**             | Privacy-preserving server architecture         | **Local-first / privacy by default**                                                                      |
| **User data location**        | Primarily on the service's infrastructure      | **Primarily on the user's machine**                                                                       |
| **Remote backup**             | Built into server infrastructure               | **Optional** - user can send selected/all local data to a remote server                                   |
| **Remote server**             | Your application's server                      | **Your server or a server the user configures/controls**                                                  |
| **Remote data transfer**      | Normal operation                               | **Explicitly initiated/consented to by the user**                                                         |
| **Protection of remote data** | Server security + encryption + access controls | **Encryption in transit, encryption at rest, access controls, and clearly defined retention/usage terms** |
| **Rust**                      | None/minimal                                   | Tauri runtime + native OS integration                                                                     |
| **Python**                    | Server-side backend                            | Local backend/sidecar                                                                                     |
| **Primary goal**              | Accessible anywhere                            | **Maximum user control and privacy**                                                                      |
