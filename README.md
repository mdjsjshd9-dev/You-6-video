# Daily Video Bot (रोज़ 6 वीडियो → GitHub → Gallery)

हर दिन GitHub Actions अपने-आप 6 वर्टिकल (9:16) वीडियो बनाता है और आपकी repo में **Release** के रूप में रख देता है।
फ़ोन में Release खोलें → वीडियो पर टैप करें → डाउनलोड → Gallery। **Telegram की ज़रूरत नहीं।** सब फ्री।

## सेटअप (फ़ोन से हो जाएगा)

1. **GitHub पर repo बनाएँ** (हो सके तो **Private**, ताकि आपके वीडियो सबको न दिखें) और इस ZIP की सारी फ़ाइलें अपलोड करें (`.github/workflows/daily.yml` समेत)।
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `GEMINI_API_KEY` (aistudio.google.com से फ्री; इससे रोज़ नई स्क्रिप्ट बनती हैं)
   - `PIXABAY_API_KEY` (pixabay.com पर फ्री अकाउंट बनाकर pixabay.com/api/docs पर key दिखती है; इससे बैकग्राउंड वीडियो आते हैं)
   - `PEXELS_API_KEY` (ऑप्शनल; Pexels अभी नई key नहीं दे रहा। दोनों हों तो पहले Pexels चलता है)
3. Repo → **Settings → Actions → General → Workflow permissions** में **Read and write permissions** चुनकर Save करें।
4. **Actions** टैब → "Daily 6 videos" → **Run workflow** (टेस्ट के लिए)।
5. 10-20 मिनट बाद repo के पेज पर दाईं ओर **Releases** में नया Release दिखेगा। उसे खोलें, नीचे **Assets** में सारे .mp4 मिलेंगे। टैप करके डाउनलोड करें।

बिना Gemini key के `scripts.json` की स्क्रिप्ट इस्तेमाल होती हैं (इसमें अपनी स्क्रिप्ट जोड़ सकते हैं)।
बिना Pexels/Pixabay key के रंगीन बैकग्राउंड बनता है।
सिर्फ़ आख़िरी 7 Release रखे जाते हैं, पुराने अपने-आप मिट जाते हैं।

(ऑप्शनल) अगर कभी Telegram चाहिए तो `TELEGRAM_BOT_TOKEN` और `TELEGRAM_CHAT_ID` secrets जोड़ दें, वरना वह बंद रहता है।

## सेटिंग्स बदलना (Settings → Secrets and variables → Actions → **Variables**)

| Variable | मतलब | उदाहरण |
|---|---|---|
| `NICHE` | वीडियो का विषय | `facts about space` |
| `VIDEOS_PER_RUN` | रोज़ कितने वीडियो | `6` |
| `VOICE` | आवाज़ | `en-US-AndrewNeural`, `en-IN-NeerjaNeural` |

समय बदलने के लिए `.github/workflows/daily.yml` में `cron` लाइन बदलें (समय UTC में है; 01:30 UTC = सुबह 7:00 IST)।

## ध्यान दें
- हिंदी आवाज़ (`hi-IN-MadhurNeural`) चल सकती है, पर वीडियो पर हिंदी कैप्शन सही न दिखें तो Variable में `CAPTIONS` = `0` रखें।
- Public repo में Release सबको दिखते हैं, इसलिए Private repo रखें। Private repo पर Actions की फ्री सीमा हर महीने 2000 मिनट है।
- Pexels/Pixabay के क्लिप फ्री-लाइसेंस के हैं, फिर भी YouTube पर डालने से पहले अपनी पॉलिसी देख लें।
- GitHub Actions फ्री प्लान में हर महीने की मिनट-सीमा होती है। 6 वीडियो रोज़ आम तौर पर उसमें आ जाते हैं, पर पहले हफ़्ते Actions टैब में टाइम चेक करें।

## लोकल टेस्ट (बिना इंटरनेट)
`DRY_RUN=1 python main.py`
