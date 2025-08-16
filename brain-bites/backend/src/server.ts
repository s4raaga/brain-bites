import 'dotenv/config';
import express from 'express';
import cors from 'cors';
import { getVideoKeys /* or getVideoIndex */ } from './videos';

const app = express();
app.use(cors());

app.get('/api/videos', async (_req, res) => {
  try {
    const keys = await getVideoKeys();
    res.json({ keys });                    
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: 'Failed to list videos' });
  }
});

const PORT = Number(process.env.PORT) || 3001; // avoid 3000 conflicts
app.listen(PORT, () => console.log(`API listening on http://localhost:${PORT}`));
