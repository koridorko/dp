

class streamTranslator():
    ### will translate RTP stream to WebRTC stream and vice versa
    def __init__(self):
        pass

    def WebRTC_to_RTP(self, data):
        ### TODO: nastudovat ako funguje RTP a WebRTC a spravit preklad, tak aby to bolo kompatibilne
        ### a zaroven reall time zvladalo prekladat streamy
        pass


    def RTP_to_WebRTC(self, data):
        ### TODO: nastudovat ako funguje RTP a WebRTC a spravit preklad, tak aby to bolo kompatibilne
        ### a zaroven reall time zvladalo prekladat streamy
        pass


    def translate(self, data, direction: str):
        """
        Direction will be int 
        0: RTP to WebRTC
        1: WebRTC to RTP                
        """
        if direction == "RTP_to_WebRTC":
            return self.RTP_to_WebRTC(data)
        elif direction == "WebRTC_to_RTP":
            return self.WebRTC_to_RTP(data)
        else:
            print("Invalid direction")
            return None
        
    