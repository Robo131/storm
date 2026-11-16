# StormScope UK (experimental, not an official warning service)

## Get the iPhone app (free, no Mac needed)
1. Create a free GitHub account and a new repository. Upload everything in this folder (keep the `.github` folder).
2. Repository > Actions > "Build IPA" > Run workflow. After ~5 minutes download the `StormScope-ipa` artifact and unzip it to get `StormScope.ipa`.
3. Install it with Sideloadly (Windows or Mac, free): connect the iPhone, drag in the .ipa, sign in with a free Apple ID, Start.
   Alternative: AltStore. On the iPhone: Settings > General > VPN & Device Management > trust your Apple ID, and turn on Developer Mode.
4. Free Apple IDs expire the app every 7 days. Re-run Sideloadly (or AltStore auto-refresh) to renew.

With a Mac you can instead run `brew install xcodegen && cd ios && xcodegen generate`, open StormScope.xcodeproj, pick your iPhone, set Team to your Apple ID, and Run.

## Storm detection backend (optional, on a computer)
    cd backend && pip install -r requirements.txt
    uvicorn main:app --host 0.0.0.0 --port 8000
In the app: Settings > Backend address > http://<computer IP>:8000 > Save. Without it the app shows RainViewer radar only.
