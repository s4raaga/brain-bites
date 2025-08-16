// DEBUG STEPS:
// 1. Check the console for 'Video URL:' logs. Copy a URL and open it in your browser. It should play the video directly.
// 2. If the video does not play in the browser, check your S3 permissions and bucket/region/key values.
// 3. If the video plays in the browser but not in the app, check your video player props and network tab for errors.

import { useEffect, useRef, useState } from 'react';
import { View, FlatList, ActivityIndicator, RefreshControl } from 'react-native';
import Video from 'react-native-video';
import Constants from 'expo-constants';

const API_BASE = 'http://localhost:3001';
const S3_BUCKET = Constants.expoConfig?.extra?.S3_BUCKET_NAME;
const S3_REGION = Constants.expoConfig?.extra?.S3_REGION;

const toS3Url = (key: string) =>
  `https://${S3_BUCKET}.s3.${S3_REGION}.amazonaws.com/` +
  key.split('/').map(encodeURIComponent).join('/');

type VideoItem = { key: string; url: string };

export default function VideoFeed() {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = async () => {
    const r = await fetch(`${API_BASE}/api/videos`);
    const data = await r.json();
    let items: VideoItem[] = [];
    if (Array.isArray(data.keys)) {
      items = data.keys
        .filter((k: string) => k.toLowerCase().endsWith('.mp4'))
        .map((k: string) => ({ key: k, url: toS3Url(k) }));
    }
    setVideos(items);
    setLoading(false);
  };

  useEffect(() => {
    load();
    timerRef.current = setInterval(load, 15000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  if (loading) return <ActivityIndicator />;

  return (
    <FlatList
      data={videos}
      keyExtractor={(item) => item.key}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
      renderItem={({ item }) => {
        console.log('Video URL:', item.url);
        return (
          <View style={{ marginBottom: 16 }}>
            <Video
              source={{ uri: item.url }}
              resizeMode="contain"
              style={{ width: '100%', height: 240 }}
              controls={false}
              paused={false}
              repeat={true}
            />
          </View>
        );
      }}
    />
  );
}
