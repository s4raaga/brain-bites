import { useEffect, useRef, useState, useCallback } from 'react';
import { Animated } from 'react-native';
import { 
  View, 
  FlatList, 
  ActivityIndicator, 
  RefreshControl, 
  Dimensions, 
  StyleSheet,
  Text,
  TouchableWithoutFeedback
} from 'react-native';
import { VideoView, useVideoPlayer } from 'expo-video';
import { LinearGradient } from 'expo-linear-gradient';
import Constants from 'expo-constants';

const API_BASE = 'https://sweet-hats-refuse.loca.lt';
const { height: SCREEN_HEIGHT, width: SCREEN_WIDTH } = Dimensions.get('window');

type VideoItem = { key: string; url: string };

interface VideoPlayerProps {
  item: VideoItem;
  isActive: boolean;
  index: number;
}

// Individual Video Player Component
const VideoPlayer = ({ item, isActive, index }: VideoPlayerProps) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [showAnimation, setShowAnimation] = useState(false);
  const [animationEmoji, setAnimationEmoji] = useState('');
  const [selectedReaction, setSelectedReaction] = useState<string | null>(null);
  const [hasInteracted, setHasInteracted] = useState(false);
  
  const animationOpacity = useRef(new Animated.Value(0)).current;
  const animationScale = useRef(new Animated.Value(0.5)).current;
  
  const player = useVideoPlayer(item.url, (player) => {
    player.loop = true;
    player.muted = true; // Start muted for autoplay
    // Preload the video even when not active
    player.preload = 'metadata';
  });

  useEffect(() => {
    const subscription = player.addListener('statusChange', (status) => {
      console.log('Video status:', status, 'for', item.key);
      if (status.isLoaded || status.readyToPlay) {
        setLoading(false);
        setError(false);
      }
      if (status.error) {
        setError(true);
        setLoading(false);
      }
    });

    // Fallback timeout to hide loading after 2 seconds
    const timeout = setTimeout(() => {
      setLoading(false);
    }, 2000);

    return () => {
      subscription?.remove();
      clearTimeout(timeout);
    };
  }, [player, item.key]);

  useEffect(() => {
    if (isActive) {
      // Autoplay when video becomes active
      const playTimeout = setTimeout(() => {
        player.play();
      }, 100);
      return () => clearTimeout(playTimeout);
    } else {
      player.pause();
    }
  }, [isActive, player]);

  // Handle unmuting after user interaction
  useEffect(() => {
    if (hasInteracted) {
      player.muted = false;
    }
  }, [hasInteracted, player]);

  // Handle touch/click to enable audio and continue playing
  const handleVideoPress = useCallback(() => {
    setHasInteracted(true);
    if (!player.playing) {
      player.play();
    }
  }, [player]);

  // Handle reaction button press with animation
  const handleReactionPress = useCallback((emoji: string) => {
    // Enable audio on first interaction
    setHasInteracted(true);
    
    // Toggle selection state
    setSelectedReaction(selectedReaction === emoji ? null : emoji);
    
    setAnimationEmoji(emoji);
    setShowAnimation(true);
    
    // Reset animation values
    animationOpacity.setValue(0);
    animationScale.setValue(0.5);
    
    // Run animation
    Animated.parallel([
      Animated.timing(animationOpacity, {
        toValue: 1,
        duration: 300,
        useNativeDriver: true,
      }),
      Animated.timing(animationScale, {
        toValue: 2,
        duration: 800,
        useNativeDriver: true,
      }),
    ]).start(() => {
      // Fade out
      Animated.timing(animationOpacity, {
        toValue: 0,
        duration: 500,
        useNativeDriver: true,
      }).start(() => {
        setShowAnimation(false);
      });
    });
  }, [animationOpacity, animationScale, selectedReaction]);

  return (
    <TouchableWithoutFeedback onPress={handleVideoPress}>
      <View style={styles.videoContainer}>
        {loading && (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="large" color="#fff" />
            <Text style={styles.loadingText}>Loading video...</Text>
          </View>
        )}
        
        {error && (
          <View style={styles.errorContainer}>
            <Text style={styles.errorText}>Failed to load video</Text>
          </View>
        )}

        <VideoView
          style={styles.video}
          player={player}
          allowsFullscreen={false}
          allowsPictureInPicture={false}
          contentFit="cover"
        />

        {/* Video overlay info with gradient shadow */}
        <View style={styles.overlay}>
          <LinearGradient
            colors={['transparent', 'rgba(0, 0, 0, 0.3)', 'rgba(0, 0, 0, 0.7)']}
            locations={[0, 0.5, 1]}
            style={styles.gradientContainer}
          >
            <Text style={styles.videoTitle} numberOfLines={2}>
              {(() => {
                const fullName = item.key.split('/').pop()?.replace('.mp4', '') || 'Untitled';
                const titlePart = fullName.split('_')[0]?.replace(/-/g, ' ') || 'Untitled';
                return titlePart.charAt(0).toUpperCase() + titlePart.slice(1);
              })()}
            </Text>
            {(() => {
              const fullName = item.key.split('/').pop()?.replace('.mp4', '') || '';
              const descriptionPart = fullName.split('_').slice(1).join(' ').replace(/-/g, ' ');
              return descriptionPart ? (
                <Text style={styles.videoDescription} numberOfLines={3}>
                  {descriptionPart.charAt(0).toUpperCase() + descriptionPart.slice(1)}
                </Text>
              ) : null;
            })()}
          </LinearGradient>
        </View>

        {/* Reaction buttons - positioned more centered */}
        <View style={styles.reactionsContainer}>
          <TouchableWithoutFeedback onPress={() => handleReactionPress('🤩')}>
            <View style={[
              styles.reactionButton,
              selectedReaction === '🤩' && styles.reactionButtonSelected
            ]}>
              <Text style={styles.reactionEmoji}>🤩</Text>
            </View>
          </TouchableWithoutFeedback>
          
          <TouchableWithoutFeedback onPress={() => handleReactionPress('😵‍💫')}>
            <View style={[
              styles.reactionButton,
              selectedReaction === '😵‍💫' && styles.reactionButtonSelected
            ]}>
              <Text style={styles.reactionEmoji}>😵‍💫</Text>
            </View>
          </TouchableWithoutFeedback>
        </View>

        {/* Reaction animation overlay */}
        {showAnimation && (
          <Animated.View style={[
            styles.animationOverlay,
            {
              opacity: animationOpacity,
              transform: [{ scale: animationScale }]
            }
          ]}>
            <Text style={styles.animationEmoji}>{animationEmoji}</Text>
          </Animated.View>
        )}
      </View>
    </TouchableWithoutFeedback>
  );
};

export default function VideoFeed() {
  const [videos, setVideos] = useState<VideoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentIndex, setCurrentIndex] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const flatListRef = useRef<FlatList>(null);

  // Load videos from API
  const load = async () => {
    try {
      const r = await fetch(`${API_BASE}/api/videos`);
      const data = await r.json();
      let items: VideoItem[] = [];
      if (Array.isArray(data.videos)) {
        items = data.videos
          .filter((video: any) => video.key.toLowerCase().endsWith('.mp4'))
          .map((video: any) => ({ key: video.key, url: video.url }));
      }
      setVideos(items);
      setLoading(false);
    } catch (error) {
      console.error('Failed to load videos:', error);
      setLoading(false);
    }
  };

  // Create stable references for FlatList callbacks
  const viewabilityConfig = useRef({
    itemVisiblePercentThreshold: 50, // Video is considered visible when 50% is visible
  }).current;

  const onViewableItemsChanged = useRef(({ viewableItems }: any) => {
    if (viewableItems.length > 0) {
      const activeIndex = viewableItems[0].index;
      setCurrentIndex(activeIndex);
      
      // Preload next videos - using a simple approach without state dependencies
      if (videos.length > 0) {
        const nextVideos = videos.slice(activeIndex + 1, activeIndex + 3);
        nextVideos.forEach(video => {
          // Mark videos for preloading (this is a placeholder for actual preloading logic)
          console.log('Preloading video:', video.key);
        });
      }
    }
  }).current;

  const renderVideoItem = ({ item, index }: { item: VideoItem; index: number }) => {
    return (
      <VideoPlayer 
        item={item} 
        isActive={index === currentIndex}
        index={index}
      />
    );
  };

  const getItemLayout = (_: any, index: number) => ({
    length: SCREEN_HEIGHT,
    offset: SCREEN_HEIGHT * index,
    index,
  });

  useEffect(() => {
    load();
    // Refresh videos every 30 seconds (less frequent than before)
    timerRef.current = setInterval(load, 30000);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  if (loading) {
    return (
      <View style={styles.loadingScreen}>
        <ActivityIndicator size="large" color="#fff" />
        <Text style={styles.loadingText}>Loading videos...</Text>
      </View>
    );
  }

  if (videos.length === 0) {
    return (
      <View style={styles.loadingScreen}>
        <Text style={styles.errorText}>No videos found</Text>
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <FlatList
        ref={flatListRef}
        data={videos}
        renderItem={renderVideoItem}
        keyExtractor={(item) => item.key}
        pagingEnabled={true} // Enable snapping to each video
        showsVerticalScrollIndicator={false}
        snapToInterval={SCREEN_HEIGHT} // Snap to each video
        snapToAlignment="start"
        decelerationRate="fast"
        onViewableItemsChanged={onViewableItemsChanged}
        viewabilityConfig={viewabilityConfig}
        getItemLayout={getItemLayout}
        removeClippedSubviews={false} // Keep videos in memory for better caching
        maxToRenderPerBatch={5} // Render more videos at once
        windowSize={10} // Keep 10 videos in memory (5 above + 5 below)
        initialNumToRender={3} // Render 3 videos initially
        refreshControl={
          <RefreshControl 
            refreshing={loading} 
            onRefresh={load}
            tintColor="#fff"
          />
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  videoContainer: {
    height: SCREEN_HEIGHT,
    width: SCREEN_WIDTH,
    backgroundColor: '#000',
    position: 'relative',
  },
  video: {
    position: 'absolute',
    top: 0,
    left: 0,
    bottom: 0,
    right: 0,
    zIndex: 0,
  },
  loadingContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#000',
    zIndex: 1,
  },
  errorContainer: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#000',
    zIndex: 1,
  },
  loadingScreen: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: '#000',
  },
  loadingText: {
    color: '#fff',
    marginTop: 10,
    fontSize: 16,
  },
  errorText: {
    color: '#fff',
    fontSize: 16,
    textAlign: 'center',
  },
  overlay: {
    position: 'absolute',
    bottom: 80,
    left: 0,
    right: 0,
    height: 120,
    zIndex: 2,
  },
  gradientContainer: {
    flex: 1,
    justifyContent: 'flex-end',
    paddingLeft: 20,
    paddingRight: 80, // Leave space for reaction buttons
    paddingBottom: 20,
  },
  videoTitle: {
    color: '#fff',
    fontSize: 18,
    fontWeight: 'bold',
    marginBottom: 4,
  },
  videoDescription: {
    color: '#fff',
    fontSize: 14,
    fontWeight: 'normal',
    opacity: 0.9,
    lineHeight: 18,
  },
  reactionsContainer: {
    position: 'absolute',
    right: 15, // Back to right edge
    top: SCREEN_HEIGHT / 2 - 60, // Vertically centered (accounting for button height)
    flexDirection: 'column',
    alignItems: 'center',
    zIndex: 3,
  },
  reactionButton: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: 'rgba(0, 0, 0, 0.4)',
    justifyContent: 'center',
    alignItems: 'center',
    marginBottom: 15,
  },
  reactionButtonSelected: {
    backgroundColor: 'rgba(128, 128, 128, 0.7)', // Lighter grey when selected
  },
  reactionEmoji: {
    fontSize: 28,
  },
  animationOverlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    justifyContent: 'center',
    alignItems: 'center',
    zIndex: 10,
    pointerEvents: 'none', // Allow touches to pass through
  },
  animationEmoji: {
    fontSize: 120,
  },
});
