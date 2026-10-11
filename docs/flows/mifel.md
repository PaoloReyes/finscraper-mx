# Mifel (Banco Mifel) — call flow

Provider id: `mifel`  
API: `sso.mifel.net` (Keycloak) + `integracion.mifel.com.mx`

## Auth

`username` + `password` — same credentials used in the Mifel Móvil app.

The provider uses the **same Keycloak `grant_type=password` endpoint** as the Mifel Móvil app.
This bypasses the web portal's Cronto push-notification MFA entirely.
`client_id` and `client_secret` are the app's public credentials (same for every user).

The access token expires in **2 minutes**. The library re-logs in on every `fetch_balances()` call.

## Flow

```mermaid
sequenceDiagram
    participant C as Caller
    participant P as Mifel provider
    participant KC as sso.mifel.net (Keycloak)
    participant I as integracion.mifel.com.mx

    C->>P: fetch_balances()
    P->>KC: POST /openid-connect/token  grant_type=password username password client_id client_secret
    KC-->>P: access_token (TTL 2 min)
    P->>KC: PUT /mobile-session-validation  Authorization: Bearer access_token
    KC-->>P: 200 deviceAllowedToLogin=true
    P->>I: POST /consultas/cuentas-vinculadas-v2  Authorization: Bearer access_token
    I-->>P: [{numCta descripcionSchemeCode moneda montoDisponible montoRetenidoCaja ...}]
    loop per account
        P->>I: GET /consultas/detalle-cuenta-vinculada/{numCta}
        I-->>P: [{saldoCajas descripcionTipoProducto ...}]
    end
    P->>P: map to list[Balance(SAVINGS)]
    P-->>C: list[Balance]
```

## Balance mapping

| Source field | Used for |
|---|---|
| `detalle.saldoCajas` | Primary balance (investment/savings bucket) → `AccountKind.SAVINGS` |
| `cuentas.montoDisponible` | Fallback if detail call fails → `AccountKind.CHECKING` |
| `cuentas.descripcionSchemeCode` | Account label (e.g. "Digital Evoluciona") |
| `cuentas.moneda` | Currency (always `"MXN"`) |

## Error handling

| Condition | Exception |
|---|---|
| Missing `username` or `password` | `AuthRequired` |
| HTTP 401 from Keycloak | `AuthRequired` |
| HTTP 5xx from Keycloak | `SessionExpired` |
| Missing `access_token` in response | `LayoutChanged` |
| HTTP 401/403 from integracion | `SessionExpired` |
| Non-zero `codigo` from integracion | `LayoutChanged` |

## Notes

- The web portal login requires a Cronto push-notification MFA on every login. The app API
  uses `grant_type=password` directly, which bypasses that challenge.
- `fingerprint` in the token request is a deterministic SHA-256 derived from the username.
  The server accepts any consistent value.
- `latitude`/`longitude` default to Mexico City. Override with `location=(lat, lon)`.
- The HAR capture (`mifel_app.har`) is gitignored; never commit it.
