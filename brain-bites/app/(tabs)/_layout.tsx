import { Tabs } from 'expo-router';
import React from 'react';
import { Platform, Image, View, StyleSheet } from 'react-native';

import { HapticTab } from '@/components/HapticTab';
import TabBarBackground from '@/components/ui/TabBarBackground';

export default function TabLayout() {
  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarButton: HapticTab,
        tabBarBackground: TabBarBackground,
        tabBarShowLabel: false,
        tabBarStyle: Platform.select({
          ios: {
            position: 'absolute',
            backgroundColor: 'rgba(0, 0, 0, 0.8)',
          },
          default: {
            backgroundColor: 'rgba(0, 0, 0, 0.8)',
          },
        }),
      }}>
      <Tabs.Screen
        name="index"
        options={{
          title: '',
          tabBarIcon: () => (
            <View style={styles.logoContainer}>
              <Image 
                source={require('@/assets/images/branding/BrainBites_logo.png')} 
                style={styles.logo} 
                resizeMode="contain"
              />
            </View>
          ),
        }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  logoContainer: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    width: '100%',
    paddingVertical: 8,
  },
  logo: {
    width: 32,
    height: 32,
    tintColor: '#FFFFFF',
  },
});
