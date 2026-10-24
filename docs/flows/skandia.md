# Skandia México — flow

Getting the credentials: [`docs/credentials.md`](../credentials.md#skandia).

Reads one `Balance` per corporate savings product (Fondo de Ahorro), in MXN, from the SKANDIA NET MX mobile
app backend (`mobile.skandia.com.mx`).

## Auth

`username` (CURP, 18 characters) + `password` (6-digit numeric PIN) — the same credentials as the web
portal and mobile app. No captcha or OTP: the app API takes the CURP and PIN directly. The JWT it returns
expires in about 20 minutes, so the provider re-logs in on every `fetch_balances()` and never caches a
session.

## Read — `fetch_balances()`

```mermaid
sequenceDiagram
  participant C as caller
  participant P as Skandia provider
  participant S as mobile.skandia.com.mx

  C->>P: fetch_balances()
  P->>S: POST /api/CoporateApp/Login/login?user=CURP&password=PIN
  S-->>P: Token JWT, Plan, PartId
  P->>S: POST /api/CoporateApp/Employees/Employees (Authorization Basic JWT, corpplan Plan)
  S-->>P: Products with ProductName, TotalAmmount, Detail
  P->>P: map Products to list of Balance (investment)
  P-->>C: list of Balance
```

## Balance mapping

One `Balance` (`kind` `investment`) per `Products` entry, using `TotalAmmount`:

```json
{
  "Products": [
    {
      "ProductName": "FONDO DE AHORRO",
      "ProductType": "FondoAhorro",
      "TotalAmmount": 50000.00,
      "Detail": [
        {"Id": "S", "Key": "APORTACION EMPLEADO", "Value": 25000.00},
        {"Id": "T", "Key": "APORTACION EMPRESA",  "Value": 25000.00}
      ]
    }
  ]
}
```

## Notes

- Errors: missing credential, login `401`, or a null `Token` / error message → `AuthRequired`; login `5xx`
  or Employees `401/403` → `SessionExpired`; missing or empty `Products` → `LayoutChanged`.
