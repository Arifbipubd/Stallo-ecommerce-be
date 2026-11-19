# Deploying Stallo on AWS (one server)

One small server runs three containers: the storefront, the API and Caddy, which serves both over HTTPS and
renews the certificates by itself. Everything is started with one `docker compose` command from this folder.

```
visitor ──https──> Caddy ──> web  (Next.js storefront)   https://SITE_DOMAIN
                         └─> api  (Django, gunicorn)     https://API_DOMAIN   (+ /admin/, /api/docs/)
                                     └─ SQLite on a Docker volume
```

Rough cost: about US$15–20 a month (a 2 GB server, 20 GB disk, one static IP). Check current AWS prices.

## 1. Create the server (EC2)

1. In the AWS console choose a region close to your customers (Singapore `ap-southeast-1` or Mumbai `ap-south-1`).
2. **EC2 → Launch instance**
   - Image: **Ubuntu Server 24.04 LTS**
   - Type: **t3.small** (2 GB). A 1 GB server often runs out of memory while building the site.
   - Key pair: create one and keep the `.pem` file safe. It is how you log in.
   - Storage: 20 GB gp3.
   - Security group (firewall): **22** from *your IP only*, **80** and **443** from anywhere.
3. **EC2 → Elastic IPs → Allocate**, then **Associate** it with the instance. Without this the IP changes
   whenever the server restarts, and everything that points at it breaks.

## 2. Install Docker

```bash
ssh -i your-key.pem ubuntu@YOUR_ELASTIC_IP
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit        # log in again so the group change applies
```

## 3. Get the code (both repos side by side)

```bash
mkdir stallo && cd stallo
git clone https://github.com/Arifbipubd/Stallo-ecommerce-be.git stallo-backend
git clone https://github.com/Arifbipubd/Stallo-ecommerce-fe.git stallo-frontend
```

If the repos are private, GitHub asks for a username and a personal access token instead of a password.

## 4. Configure

```bash
cd stallo-backend/deploy
cp .env.example .env
nano .env
```

- **No domain yet:** keep the `sslip.io` names, but put *your* Elastic IP in them with dots changed to dashes.
  For IP `13.250.10.20`: `SITE_DOMAIN=13-250-10-20.sslip.io` and `API_DOMAIN=api.13-250-10-20.sslip.io`.
  sslip.io is a free service that turns such a name back into the IP, which is enough for a real HTTPS certificate.
  If the certificate is refused (shared rate limit), wait an hour and retry, or use a real domain.
- **With a domain:** create two DNS **A records** pointing at the Elastic IP (`stallo.com` and `api.stallo.com`)
  and use those names.
- `DJANGO_SECRET_KEY`: run `openssl rand -hex 32` and paste the result.
- `DJANGO_SUPERUSER_PASSWORD`: choose a long one. It becomes the admin login on the first start.

## 4b. Product pictures (S3 bucket)

Pictures uploaded in admin (**Menu items → Picture**) are stored in an S3 bucket; the database keeps only each file's
name, and the API hands the storefront the full public address. Do this once, before you start the site:

1. **Create the bucket** (S3 → Create bucket). Pick the same region as the server (e.g. `ap-southeast-1`), a unique
   name such as `stallo-media`, and **untick "Block all public access"** (only reading pictures is made public, below).
   Leave "ACLs disabled" as it is.
2. **Let the world read the pictures** (bucket → Permissions → Bucket policy), replacing `stallo-media`:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Sid": "PublicReadMenuPictures",
       "Effect": "Allow",
       "Principal": "*",
       "Action": "s3:GetObject",
       "Resource": "arn:aws:s3:::stallo-media/menu/*"
     }]
   }
   ```

   Only the `menu/` folder is public, nothing else in the bucket.
3. **Let the server write to it, with no keys:** IAM → Policies → Create policy (JSON), then IAM → Roles → create a role
   for **EC2** with that policy and attach it to your instance (EC2 → Instance → Actions → Security → Modify IAM role).
   The same role can also carry the backup permission from step 6.

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["s3:PutObject", "s3:DeleteObject"],
       "Resource": "arn:aws:s3:::stallo-media/menu/*"
     }]
   }
   ```

4. In `.env` set `AWS_STORAGE_BUCKET_NAME=stallo-media` and `AWS_S3_REGION_NAME=ap-southeast-1` (your region).
   Optional: put CloudFront in front and set `AWS_S3_CUSTOM_DOMAIN` to its address.

Leave `AWS_STORAGE_BUCKET_NAME` empty and pictures are saved on the server's disk instead, which also works for a
trial but is lost if the server is rebuilt. Uploads are resized (longest side 1200 px, WebP) before they are stored, so
a phone photo becomes about 100 KB. Replacing or deleting a picture, or deleting the product, removes the old file.

## 5. Start it

```bash
docker compose up -d --build
docker compose ps          # all three should be running (api and web turn "healthy" after a minute)
docker compose logs -f caddy   # watch the certificates being issued; Ctrl+C to leave
```

The first build takes several minutes. Then:

- Site: `https://SITE_DOMAIN`
- Admin: `https://API_DOMAIN/admin/` (the username and password from `.env`)
- API docs: `https://API_DOMAIN/api/docs/`

On the very first start the menu and outlets are loaded once. After that, nothing overwrites your admin edits.
In admin, open **Today's offers** and set the offer prices.

Place a test order from the site, check it appears under **Orders** in admin, then delete it.

## 6. Backups (do this on day one)

The database is one file and holds every order. Two layers, both worth having:

- **EBS snapshots** (easiest): EC2 → Elastic Block Store → Lifecycle Manager → create a daily snapshot policy for the
  server's volume.
- **`./backup.sh`**: makes a consistent copy of the database into `~/stallo-backups` (keeps 14 days). Run it nightly:

  ```bash
  crontab -e
  # add this line (2:30 am server time):
  30 2 * * * /home/ubuntu/stallo/stallo-backend/deploy/backup.sh >> /home/ubuntu/stallo-backup.log 2>&1
  ```

  To also copy each backup to S3: create a bucket, give the server an IAM role that may `s3:PutObject` on it
  (EC2 → Instance → Actions → Security → Modify IAM role), install the AWS CLI (`sudo snap install aws-cli --classic`)
  and set `BACKUP_S3_BUCKET=your-bucket` in `.env`.

To restore: stop the API, copy the unzipped backup over `/data/db.sqlite3` in the `stallo-data` volume, start it again.

## 7. Updating the site

```bash
cd ~/stallo/stallo-backend && git pull
cd ~/stallo/stallo-frontend && git pull
cd ~/stallo/stallo-backend/deploy && docker compose up -d --build
```

Database changes (migrations) are applied automatically when the API starts.

## 8. Moving to a real domain later

Point the two A records at the Elastic IP, change `SITE_DOMAIN` and `API_DOMAIN` in `.env`, then run
`docker compose up -d --build`. The storefront must be rebuilt because the API address is baked into it.

## Things to know

- **Nobody is notified of new orders.** Staff must watch the admin **Orders** page (or call to confirm, as the site
  tells customers). Email/SMS/WhatsApp alerts are a good next feature.
- **One server, one database.** Fine for a shop of this size. If you outgrow it, move to PostgreSQL (RDS) before
  running more than one server.
- **Logs:** `docker compose logs -f api` (orders, errors) and `docker compose logs -f web`.
- **Keep secrets out of git.** `.env` is ignored. Anyone with the `.pem` key or `.env` has full access.
- **Security group:** keep SSH (22) limited to your own IP.
- Legal pages (Terms, Privacy) are drafts and should be reviewed before launch.
