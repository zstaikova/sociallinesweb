# LinkedIn Integration Setup

Connect Socialline to a LinkedIn Company Page for posting articles and images.

---

## Prerequisites

- A LinkedIn account
- A **LinkedIn Company Page** (not a personal profile) — you must be a Page Admin
- A LinkedIn developer app associated with your Company Page
- Socialline running at `http://localhost:5000`

---

## LinkedIn App Ownership

> **Important:** A LinkedIn developer app is owned by a **person** (personal LinkedIn account), not by a company or organization. The Company Page is associated with the app, but the app itself belongs to whoever created it at linkedin.com/developers.

---

## Create a LinkedIn Developer App

1. Go to https://linkedin.com/developers → **Create app**
2. Fill in:
   - **App name**: e.g. `Socialline`
   - **LinkedIn Page**: select or create your Company Page (see below)
   - **App logo**: required — upload a square image
3. Agree to terms and click **Create app**
4. From the app dashboard → **Auth** tab → copy **Client ID** and **Client Secret**

Add to `.env`:
```env
LINKEDIN_CLIENT_ID=<client id>
LINKEDIN_CLIENT_SECRET=<client secret>
```

---

## Create a LinkedIn Company Page (if needed)

LinkedIn requires a Company Page (not a personal profile or Showcase Page) to associate with a developer app.

1. Go to linkedin.com → **Work** (grid icon) → **Create a Company Page**
2. Choose **Company** type
3. Fill in company name, industry, size, and a unique page URL (e.g. `socialline-space`)
4. LinkedIn company page URLs must be globally unique across LinkedIn

> **Gotcha — page name taken:** If your desired page name is taken, LinkedIn page URLs (not display names) must also be unique. If `socialline` is taken, use a variation like `socialline-space`. The display name can still be "Socialline."

---

## Associate the Company Page with the App

After creating the app:

1. In the app dashboard → **Settings** tab → under **App settings** verify the associated page is correct
2. The page admin must verify the association: they receive a request under LinkedIn Page admin → **Settings** → **Developer tools**

---

## Add Required Products

1. In the app dashboard → **Products** tab
2. Request access to **Community Management API** (for posting to Company Pages)
3. This gives the scopes needed:
   - `w_organization_social` — post on behalf of a Company Page
   - `r_organization_social` — read Company Page posts

> **Gotcha — personal profile posting:** The Community Management API is for posting to Company Pages, not personal profiles. Socialline does not support posting to personal LinkedIn profiles.

---

## Configure OAuth Redirect URI

1. In the app dashboard → **Auth** tab → **OAuth 2.0 settings**
2. Add **Authorized redirect URL**:
   ```
   http://localhost:5000/callback
   ```
3. Save

---

## Connect via Socialline Web UI

1. Go to `http://localhost:5000` → select your brand → **Accounts**
2. Click **Connect** next to LinkedIn
3. A LinkedIn authorization window opens
4. Approve the permissions
5. Socialline exchanges the code for an access token and stores it in the brand's AccountStore

---

## Showcase Pages

LinkedIn **Showcase Pages** are child pages of a Company Page, used for specific products or initiatives (e.g. "Cognify Learn" as a showcase of a "Sociallines" parent page).

- A Showcase Page can be associated with a developer app only if you are also admin of the parent Company Page
- Posting to a Showcase Page uses the same `w_organization_social` scope but with the Showcase Page's organization ID

---

## Credential Storage

| Key | Value |
|---|---|
| `LINKEDIN_ACCESS_TOKEN` | OAuth 2.0 access token |
| `LINKEDIN_ORGANIZATION_ID` | Numeric Company/Showcase Page ID |

---

## Gotchas and Known Issues

- **"LinkedIn app not configured"** — `LINKEDIN_CLIENT_ID` or `LINKEDIN_CLIENT_SECRET` is missing from `.env`. Add them and restart the server.
- **"Unable to add page" / "Unknown error"** — LinkedIn's app association flow sometimes fails silently. Wait a few minutes and retry; LinkedIn's admin tooling has eventual consistency issues.
- **Token expiry** — LinkedIn access tokens expire after 60 days. Reconnect via the web UI when posting fails with a 401.
- **App review** — The Community Management API may require app review for production use beyond development/testing. LinkedIn's review process requires a description of use case and a live demo.
- **Personal vs. company posting** — LinkedIn strictly separates personal profile tokens from Organization tokens. Using a personal token to post to a Company Page returns a 403. Ensure you authorize as a Page Admin.
